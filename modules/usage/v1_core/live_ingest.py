#!/usr/bin/env python3
"""Local V1 ingestion. Legacy collector remains authoritative."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

B = Path("/opt/la-portal")
DB = Path("/var/lib/xray-usage-v1/usage.db")
STATE = Path("/var/lib/xray-usage/current.json")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from counter_engine import ingest, utc
from fusion import resolve
from shadow_reconcile import SOURCES, canonical, source_counters

def totals(state):
    result = defaultdict(lambda: [0, 0])
    for raw, data in state["users"].items():
        key = canonical(raw).casefold()
        for i, direction in enumerate(("uplink", "downlink")):
            value = data.get(direction, 0)
            if type(value) is not int or value < 0:
                raise ValueError("Invalid legacy total")
            result[key][i] += value
    return {
        k: tuple(v) for k, v in result.items()
        if any(v)
    }

def check(db, state, period):
    expected = totals(state)
    actual = {
        name.casefold(): (up, down)
        for name, up, down in db.execute("""
            SELECT canonical_name,upload_bytes,download_bytes
            FROM v1_account_period WHERE period_id=?
        """, (period,))
        if up or down
    }
    if expected != actual:
        names = sorted(
            k for k in set(expected) | set(actual)
            if expected.get(k, (0, 0)) != actual.get(k, (0, 0))
        )
        raise RuntimeError(
            "V1_ACCOUNT_RECONCILIATION_FAILED: " + ",".join(names)
        )

    pending = db.execute("""
        SELECT COALESCE(SUM(total_bytes),0)
        FROM v1_unmapped_hourly WHERE period_id=?
    """, (period,)).fetchone()[0]

    if pending:
        raise RuntimeError("Unmapped traffic detected")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity failure")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Foreign key failure")
    return sum(sum(v) for v in actual.values())

def run(inventory=None):
    state = json.loads(STATE.read_text())
    stamp = state["last_updated"]
    period = state["period_started"]
    observed = utc(stamp)

    src = sqlite3.connect(
        DB.resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        active = src.execute("""
            SELECT period_id FROM billing_periods
            WHERE ended_at_utc IS NULL
        """).fetchall()
        last = src.execute("""
            SELECT value FROM v1_live_meta
            WHERE key='last_state_at'
        """).fetchone()[0]

        if active != [(period,)]:
            raise RuntimeError(
                "BILLING_PERIOD_CHANGED: V1 paused safely"
            )
        if observed < utc(last):
            raise RuntimeError("Legacy timestamp moved backwards")

        if observed == utc(last):
            total = check(src, state, period)
            print("V1_BASELINE_PASS", "bytes:", total)
            return

        fd, temp = tempfile.mkstemp(
            prefix=".v1-ingest-", suffix=".db",
            dir=str(DB.parent)
        )
        os.close(fd)
        staged = None
        try:
            staged = sqlite3.connect(temp, timeout=30)
            src.backup(staged)
            staged.execute("PRAGMA foreign_keys=ON")
            if inventory is not None:
                from lifecycle import synchronize
                synchronize(staged, period, inventory)

            # New identities start from zero, matching legacy collection.
            # All writes here affect the temporary database only.
            for sid, genkey, registry, prefixes in SOURCES:
                generation = state["collector_generations"][genkey]
                for raw, direction, value in source_counters(
                    state, registry
                ):
                    exists = staged.execute("""
                        SELECT 1 FROM source_counter_state
                        WHERE source_id=? AND raw_identity=?
                          AND direction=?
                    """, (sid, raw, direction)).fetchone()
                    if not exists:
                        # The FK parent must exist before seeding a new counter.
                        # Resolve in the staged copy so failed reconciliation
                        # leaves the live database untouched.
                        resolve(staged, sid, raw, prefixes)
                        staged.execute("""
                            INSERT INTO source_counter_state
                            (source_id,raw_identity,direction,
                             generation_id,last_counter_bytes,
                             updated_at_utc)
                            VALUES (?,?,?,?,0,?)
                        """, (sid, raw, direction, generation, last))
            staged.commit()

            added = 0
            for sid, genkey, registry, prefixes in SOURCES:
                batch = "local-" + hashlib.sha256(
                    (sid + "|" + stamp).encode()
                ).hexdigest()
                _, delta = ingest(
                    db=staged,
                    source_id=sid,
                    batch_id=batch,
                    generation=state["collector_generations"][genkey],
                    observed_at=stamp,
                    counters=source_counters(state, registry),
                    period_id=period,
                    trusted_prefixes=prefixes
                )
                added += delta

            total = check(staged, state, period)
            staged.execute("""
                UPDATE v1_live_meta SET value=?
                WHERE key='last_state_at'
            """, (stamp,))
            staged.commit()
            staged.close()
            staged = None

            os.chmod(temp, 0o600)
            os.replace(temp, DB)
            directory_fd = os.open(DB.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

            print("V1_INGEST_PASS",
                  "added:", added,
                  "account_total:", total,
                  "stamp:", stamp)
        finally:
            if staged is not None:
                staged.close()
            if os.path.exists(temp):
                os.unlink(temp)
    finally:
        src.close()

if __name__ == "__main__":
    run()
