#!/usr/bin/env python3
"""Candidate only. Uses the existing external xray-usage.lock protocol."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone

XRAY = "/etc/v2ray-agent/xray/xray"
STATE_FILE = "/var/lib/xray-usage/current.json"
ENABLE_FILE = "/etc/xray-xhttp443/collection-enabled"
EXTRA_COUNTERS = "xhttp443_last_counters"
GENERATIONS = "collector_generations"


def log_warning(message):
    try:
        import syslog
        syslog.openlog("xray-usage")
        syslog.syslog(syslog.LOG_WARNING, message)
    except (ImportError, OSError):
        pass
    print(message, file=sys.stderr)


def parse_counters(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("stat", []), list):
        raise ValueError("Invalid stats response")
    counters = {}
    for item in payload.get("stat", []):
        if not isinstance(item, dict):
            raise ValueError("Invalid stats item")
        name = item.get("name", "")
        if not isinstance(name, str) or not name.startswith("user>>>"):
            continue
        parts = name.split(">>>")
        if len(parts) != 4 or parts[2] != "traffic" or parts[3] not in ("uplink", "downlink"):
            continue
        if not parts[1]:
            raise ValueError("Empty user identity")
        raw = item.get("value", 0)
        if isinstance(raw, bool):
            raise ValueError("Invalid counter")
        current = int(raw)
        if current < 0 or (isinstance(raw, float) and raw != current):
            raise ValueError("Invalid counter")
        if name in counters and counters[name] != current:
            raise ValueError("Conflicting duplicate counter")
        counters[name] = current
    return counters


def apply_samples(state, samples, observed_at=None):
    """Each sample was taken within one stable process generation.

    Absent samples leave that source's counters untouched. Legacy main counters
    keep the existing first-run baseline; no retroactive reset is inferred.
    """
    result = copy.deepcopy(state)
    users = result.setdefault("users", {})
    generations = result.setdefault(GENERATIONS, {})
    for source, sample in samples.items():
        if source not in ("main", "xhttp443", "hy2"):
            raise ValueError("Unknown source")
        generation, payload = sample
        if not isinstance(generation, str) or not generation:
            raise ValueError("Missing stable process generation")
        counters = parse_counters(payload)
        counter_key = {"main": "last_counters", "xhttp443": EXTRA_COUNTERS, "hy2": "hy2_last_counters"}[source]
        previous = result.setdefault(counter_key, {})
        prior_generation = generations.get(source)
        if prior_generation is not None and prior_generation != generation:
            # Clear even counters absent from this response: they belong to the
            # old process and may only appear after that user connects again.
            previous.clear()
        for name, current in counters.items():
            old = int(previous.get(name, 0))
            if old < 0:
                raise ValueError("Invalid stored counter")
            delta = current - old if current >= old else current
            _, user, _, direction = name.split(">>>")
            entry = users.setdefault(user, {"uplink": 0, "downlink": 0})
            total = int(entry.get(direction, 0))
            if total < 0:
                raise ValueError("Invalid stored total")
            entry[direction] = total + delta
            previous[name] = current
        generations[source] = generation
    if samples:
        result["last_updated"] = observed_at or datetime.now(timezone.utc).isoformat()
    return result


def process_generation(unit):
    response = subprocess.run(
        ["systemctl", "show", unit, "-p", "MainPID", "--value"],
        capture_output=True, text=True, timeout=4, check=True,
    )
    pid = int(response.stdout.strip())
    if pid <= 0:
        raise RuntimeError("API service is not running")
    text = Path(f"/proc/{pid}/stat").read_text()
    start_ticks = text.rsplit(")", 1)[1].split()[19]
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return f"{boot}:{pid}:{start_ticks}"


def read_sample(endpoint, unit):
    before = process_generation(unit)
    response = subprocess.run(
        [XRAY, "api", "statsquery", f"--server={endpoint}"],
        capture_output=True, text=True, timeout=8, check=True,
    )
    payload = json.loads(response.stdout)
    parse_counters(payload)
    if process_generation(unit) != before:
        raise RuntimeError("API process changed during sampling")
    return before, payload


def read_hy2_sample():
    # Fixed loopback endpoint, no reset parameter, stable process generation.
    config = json.loads(Path('/etc/hy2-udp443/server.private.json').read_text())
    mapping = json.loads(Path('/etc/hy2-udp443/accounts.private.json').read_text())['users']
    if config['trafficStats']['listen'] != '127.0.0.1:10087':
        raise ValueError('Hy2 API scope mismatch')
    before = process_generation('hy2-udp443.service')
    request = urllib.request.Request('http://127.0.0.1:10087/traffic',
        headers={'Authorization': config['trafficStats']['secret']})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=4) as response:
        data = json.load(response)
    payload = hy2_payload(data, mapping)
    if process_generation('hy2-udp443.service') != before:
        raise RuntimeError('Hy2 process changed during sampling')
    return before, payload


def hy2_payload(data, mapping):
    if not isinstance(data, dict) or not isinstance(mapping, dict):
        raise ValueError('Invalid Hy2 response or mapping')
    # Hysteria userpass authentication lowercases usernames before exposing stats.
    # Normalize only login lookup; preserve the canonical accounting identity.
    normalized = {}
    for login, record in mapping.items():
        if not isinstance(login, str) or not login or login.lower() in normalized:
            raise ValueError('Invalid or case-colliding Hy2 login mapping')
        normalized[login.lower()] = record
    result = []
    seen = set()
    for login, values in data.items():
        if not isinstance(login, str) or login.lower() not in normalized or not isinstance(values, dict):
            raise ValueError('Unexpected Hy2 account')
        identity = normalized[login.lower()]['counter_user']
        if not isinstance(identity, str) or not identity or '>>>' in identity or identity in seen:
            raise ValueError('Invalid or overlapping Hy2 account mapping')
        seen.add(identity)
        # Hysteria 2.13: Tx server->target = client upload; Rx = download.
        for field, direction in (('tx', 'uplink'), ('rx', 'downlink')):
            value = values.get(field)
            if type(value) is not int or value < 0:
                raise ValueError('Invalid Hy2 counter')
            result.append({'name': f'user>>>{identity}>>>traffic>>>{direction}', 'value': value})
    return {'stat': result}


def save_state(state, path=STATE_FILE):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".current-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def collect():
    # Called through the existing cron flock or while the monthly script holds
    # that same lock. Acquiring a second independent flock here would deadlock.
    with open(STATE_FILE, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or not state.get("period_started"):
        raise ValueError("Existing state missing; refusing to recreate history")
    sources = {"main": ("127.0.0.1:10085", "xray.service")}
    if Path(ENABLE_FILE).is_file():
        sources["xhttp443"] = ("127.0.0.1:10086", "xray-xhttp443.service")
    samples, errors = {}, []
    for source, (endpoint, unit) in sources.items():
        try:
            samples[source] = read_sample(endpoint, unit)
        except Exception as error:
            # Never log exception text containing RPC output or user identities.
            errors.append((source, type(error).__name__))
    if Path('/etc/hy2-udp443/collection-enabled').is_file():
        try:
            samples['hy2'] = read_hy2_sample()
        except Exception as error:
            errors.append(('hy2', type(error).__name__))
    if not samples:
        raise RuntimeError("No API available; state left unchanged")
    save_state(apply_samples(state, samples))
    for source, category in errors:
        log_warning(f"Sampling incomplete: {source} ({category}); baseline retained")
    print("State saved; successful sources:", ", ".join(sorted(samples)))
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(collect())
    except Exception as error:
        log_warning("Collection failed: " + type(error).__name__)
        raise SystemExit(1)
