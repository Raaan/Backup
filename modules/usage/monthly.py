#!/usr/bin/env python3
"""V1 monthly rollover. Legacy collector remains the counter source."""
import fcntl
import json
import os
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, "/opt/la-portal/v1_core")
from billing import load as load_billing
from lifecycle import read_inventory, retire_absent

BASE = Path("/opt/la-portal")
DB = Path("/var/lib/xray-usage-v1/usage.db")
STATE = Path("/var/lib/xray-usage/current.json")
HISTORY = Path("/var/lib/xray-usage/history")
INGEST = BASE / "v1_core/live_ingest.py"
RESET = Path("/root/xray-monthly-reset.sh")
LOCK = Path("/var/lock/xray-usage-v1.lock")
ARCHIVE = Path("/root/xray-v1-monthly-archives")
MAIN = "fair-light-1/xray-main"

def read_json(path):
    return json.loads(path.read_text())

def active(db):
    rows = db.execute("""
        SELECT period_id FROM billing_periods
        WHERE ended_at_utc IS NULL
    """).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one active period")
    return rows[0][0]

def validate(db):
    db.execute("PRAGMA foreign_keys=ON")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity failure")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Foreign key failure")
    active(db)
    if not db.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='v1_live_meta'
    """).fetchone():
        raise RuntimeError("Missing live metadata")
    if not db.execute("""
        SELECT 1 FROM source_counter_state
        WHERE source_id=? LIMIT 1
    """, (MAIN,)).fetchone():
        raise RuntimeError("Main counter state missing")

def backup():
    folder = ARCHIVE / datetime.now(timezone.utc).strftime(
        "%Y%m%d-%H%M%S-%f"
    )
    folder.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(DB) as src:
        with sqlite3.connect(folder / "usage.db") as dst:
            src.backup(dst)
    shutil.copy2(STATE, folder / "current.json")
    os.chmod(folder / "usage.db", 0o600)
    os.chmod(folder / "current.json", 0o600)
    print("BACKUP:", folder, flush=True)
    return folder

def final_history(period):
    candidates = []
    for path in HISTORY.glob("*.json"):
        try:
            state = read_json(path)
            if state.get("period_started") == period:
                candidates.append(
                    (datetime.fromisoformat(state["last_updated"]), path)
                )
        except (OSError, ValueError, KeyError):
            continue
    if not candidates:
        raise RuntimeError("Final legacy archive missing")
    return max(candidates)[1]

def ingest_from(path):
    sys.path.insert(0, str(BASE / "v1_core"))
    module = runpy.run_path(
        str(INGEST), run_name="v1_monthly_import"
    )
    runner = module["run"]
    if Path(runner.__globals__["DB"]) != DB:
        raise RuntimeError("Live ingester uses another database")
    runner.__globals__["STATE"] = path
    runner(inventory=read_inventory() if path == STATE else None)

def transition(db, old, new):
    if datetime.fromisoformat(new) <= datetime.fromisoformat(old):
        raise RuntimeError("New period must be later")

    validate(db)
    if active(db) != old:
        raise RuntimeError("Active period changed")

    before = db.execute("""
        SELECT COALESCE(SUM(total_bytes),0)
        FROM v1_account_period
        WHERE period_id=?
    """, (old,)).fetchone()[0]

    with db:
        db.execute("""
            UPDATE billing_periods SET ended_at_utc=?
            WHERE period_id=? AND ended_at_utc IS NULL
        """, (new, old))

        db.execute("""
            INSERT INTO billing_periods
            (period_id,started_at_utc,ended_at_utc,
             legacy_baseline_bytes)
            VALUES (?,?,NULL,0)
        """, (new, new))

        # Only Main is reset by the legacy monthly reset.
        retire_absent(db, new)
        # XHTTP/HY2 must retain their existing counter baselines.
        db.execute("""
            UPDATE source_counter_state
            SET last_counter_bytes=0
            WHERE source_id=?
        """, (MAIN,))

        after = db.execute("""
            SELECT COALESCE(SUM(total_bytes),0)
            FROM v1_account_period WHERE period_id=?
        """, (old,)).fetchone()[0]

        if before != after:
            raise RuntimeError("Historical totals changed")
        if active(db) != new:
            raise RuntimeError("New period not active")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Transition FK failure")

    validate(db)
    print("PERIOD_TRANSITION_PASS:", new, flush=True)

def simulate():
    with tempfile.TemporaryDirectory(prefix="v1-monthly-check-") as tmp:
        path = Path(tmp) / "simulation.db"
        with sqlite3.connect(DB) as src:
            with sqlite3.connect(path) as dst:
                src.backup(dst)

        with sqlite3.connect(path) as db:
            old = active(db)
            new = "2099-01-01T00:00:00+00:00"
            transition(db, old, new)

            main = db.execute("""
                SELECT COALESCE(SUM(last_counter_bytes),0)
                FROM source_counter_state WHERE source_id=?
            """, (MAIN,)).fetchone()[0]

            if main != 0:
                raise RuntimeError("Main was not reset")

            count = db.execute("""
                SELECT COUNT(*) FROM billing_periods
                WHERE ended_at_utc IS NULL
            """).fetchone()[0]
            if count != 1:
                raise RuntimeError("Incorrect active period count")

    print("MONTHLY_V2_SIMULATION_PASSED")

def production():
    with sqlite3.connect(DB) as db:
        validate(db)
        old = active(db)

    legacy = read_json(STATE)
    new = legacy["period_started"]

    if old == new:
        # Guard matches the same configured day/timezone as the timer.
        policy = load_billing()
        if datetime.now(ZoneInfo(policy['timezone'])).day != policy['day']:
            raise RuntimeError('Reset allowed only on configured billing day')

        # Synchronize the latest available legacy sample first.
        ingest_from(STATE)
        backup()

        # This command performs the actual Xray Main reset.
        # Its existing legacy lock serializes legacy collection.
        subprocess.run([str(RESET)], check=True)

        legacy = read_json(STATE)
        new = legacy["period_started"]

        if old == new:
            raise RuntimeError("Legacy reset did not advance period")

    # Recovery path after an already completed legacy reset.
    # Never reset Main twice if legacy has advanced.
    if datetime.fromisoformat(new) <= datetime.fromisoformat(old):
        raise RuntimeError("Legacy period is not newer")

    archive = final_history(old)
    ingest_from(archive)

    with sqlite3.connect(DB, timeout=30) as db:
        transition(db, old, new)

    if read_json(STATE)["period_started"] != new:
        raise RuntimeError("Post-transition state mismatch")

    print("V1_MONTHLY_V2_COMPLETE:", new)

def main():
    if os.geteuid() != 0:
        raise RuntimeError("Root required")

    if "--check" in sys.argv:
        with LOCK.open("a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            simulate()
        return

    with LOCK.open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        production()

if __name__ == "__main__":
    main()
