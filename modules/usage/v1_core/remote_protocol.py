"""V1 remote push authentication. No HTTP listener."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone

PATH = "/v1/ingest"
MAX_BYTES = 1024 * 1024
WINDOW = 300


def instant(value):
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        raise ValueError("Invalid timestamp") from None
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def sign(body, source, key_id, sent_at, secret):
    if not isinstance(body, bytes):
        raise TypeError("Body must be bytes")
    material = (
        "POST\n" + PATH + "\n" +
        source + "\n" + key_id + "\n" +
        sent_at + "\n" +
        hashlib.sha256(body).hexdigest()
    ).encode()
    return {
        "X-Source-ID": source,
        "X-Key-ID": key_id,
        "X-Sent-At": sent_at,
        "X-Signature": hmac.new(
            secret, material, hashlib.sha256
        ).hexdigest()
    }


def verify(body, headers, credentials, now=None):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise ValueError("Invalid body size")

    now = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    source = headers.get("X-Source-ID")
    key_id = headers.get("X-Key-ID")
    sent_at = headers.get("X-Sent-At")
    signature = headers.get("X-Signature")

    if not all(
        isinstance(x, str) and x
        for x in (source, key_id, sent_at, signature)
    ):
        raise ValueError("Missing authentication headers")

    registered = credentials.get(source)
    if not registered or registered["key_id"] != key_id:
        raise ValueError("Unknown source or key")

    if abs((now - instant(sent_at)).total_seconds()) > WINDOW:
        raise ValueError("Expired request")

    expected = sign(
        body, source, key_id, sent_at,
        registered["secret"]
    )["X-Signature"]

    if (
        not re.fullmatch(r"[0-9a-f]{64}", signature)
        or not hmac.compare_digest(signature, expected)
    ):
        raise ValueError("Invalid signature")

    data = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=unique_keys
    )

    fields = {
        "schema_version", "source_id", "batch_id",
        "generation_id", "observed_at", "counters"
    }

    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("Invalid payload structure")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported version")
    if data["source_id"] != source:
        raise ValueError("Source mismatch")
    if not isinstance(data["batch_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9._:-]{1,128}", data["batch_id"]
    ):
        raise ValueError("Invalid batch ID")
    if (
        not isinstance(data["generation_id"], str)
        or not 1 <= len(data["generation_id"]) <= 256
    ):
        raise ValueError("Invalid generation")

    if instant(data["observed_at"]) > now + timedelta(seconds=WINDOW):
        raise ValueError("Future sample")

    counters = data["counters"]
    if not isinstance(counters, list) or not 1 <= len(counters) <= 5000:
        raise ValueError("Invalid counter list")

    seen = set()
    for item in counters:
        if not isinstance(item, dict) or set(item) != {
            "raw_identity", "direction", "cumulative_bytes"
        }:
            raise ValueError("Invalid counter structure")

        raw = item["raw_identity"]
        direction = item["direction"]
        value = item["cumulative_bytes"]

        if not isinstance(raw, str) or not 1 <= len(raw) <= 512 or ">>>" in raw:
            raise ValueError("Invalid account")
        if direction not in ("uplink", "downlink"):
            raise ValueError("Invalid direction")
        if type(value) is not int or value < 0:
            raise ValueError("Invalid counter")
        if (raw, direction) in seen:
            raise ValueError("Duplicate counter")

        seen.add((raw, direction))

    return data
