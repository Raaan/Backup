"""V1 counter engine: atomic, source-scoped, idempotent."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from fusion import resolve


def utc(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return dt.astimezone(timezone.utc)


def ingest(db, source_id, batch_id, generation,
           observed_at, counters, period_id, trusted_prefixes,
           authorized_reset=False):
    """Consume cumulative counters. The caller supplies a trusted source."""
    if not all(isinstance(x, str) and x
               for x in (source_id, batch_id, generation, period_id)):
        raise ValueError("Invalid batch metadata")

    observed = utc(observed_at)
    stamp = observed.isoformat()

    if not isinstance(counters, list):
        raise ValueError("Counters must be a list")

    seen = set()
    for raw, direction, value in counters:
        if not isinstance(raw, str) or not raw or ">>>" in raw:
            raise ValueError("Invalid raw identity")
        if direction not in ("uplink", "downlink"):
            raise ValueError("Invalid direction")
        if type(value) is not int or value < 0:
            raise ValueError("Invalid counter")
        if (raw, direction) in seen:
            raise ValueError("Duplicate counter")
        seen.add((raw, direction))

    ordered = sorted(counters)
    content = json.dumps(
        [source_id, period_id, generation, stamp, ordered],
        separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(content.encode()).hexdigest()

    if db.in_transaction:
        raise RuntimeError("Unexpected open transaction")

    db.execute("BEGIN IMMEDIATE")
    try:
        source = db.execute("""
            SELECT enabled FROM sources WHERE source_id=?
        """, (source_id,)).fetchone()
        if not source or source[0] != 1:
            raise ValueError("Unknown or disabled source")

        periods = db.execute("""
            SELECT period_id,started_at_utc,ended_at_utc
            FROM billing_periods
        """).fetchall()

        active = [
            pid for pid, started, ended in periods
            if utc(started) <= observed
            and (ended is None or observed < utc(ended))
        ]
        if len(active) != 1 or active[0] != period_id:
            raise ValueError(
                "Billing period not uniquely active"
            )

        old_batch = db.execute("""
            SELECT payload_sha256 FROM collection_batches
            WHERE source_id=? AND batch_id=?
        """, (source_id, batch_id)).fetchone()

        if old_batch:
            if old_batch[0] != digest:
                raise ValueError("Conflicting batch replay")
            db.commit()
            return "DUPLICATE", 0

        latest = db.execute("""
            SELECT observed_at_utc
            FROM collection_batches WHERE source_id=?
            ORDER BY observed_at_utc DESC LIMIT 1
        """, (source_id,)).fetchone()

        if latest and observed <= utc(latest[0]):
            raise ValueError("Out-of-order batch")

        hour = observed.replace(
            minute=0, second=0, microsecond=0
        ).isoformat()

        added = 0
        for raw, direction, current in ordered:
            account_id, status = resolve(
                db, source_id, raw, trusted_prefixes
            )

            previous = db.execute("""
                SELECT last_counter_bytes,generation_id,
                       updated_at_utc
                FROM source_counter_state
                WHERE source_id=? AND raw_identity=? AND direction=?
            """, (source_id, raw, direction)).fetchone()

            if previous is None:
                # New source-account: first sample sets baseline.
                delta = 0
            else:
                old, old_gen, previous_at = previous
                if observed <= utc(previous_at):
                    raise ValueError("Stale counter sample")
                if generation != old_gen:
                    delta = current
                elif current >= old:
                    delta = current - old
                elif authorized_reset:
                    delta = current
                else:
                    raise ValueError("Unverified counter decrease")

            db.execute("""
                INSERT INTO source_counter_state
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(source_id,raw_identity,direction)
                DO UPDATE SET
                  generation_id=excluded.generation_id,
                  last_counter_bytes=excluded.last_counter_bytes,
                  updated_at_utc=excluded.updated_at_utc
            """, (
                source_id, raw, direction,
                generation, current, stamp
            ))

            if delta:
                upload = delta if direction == "uplink" else 0
                download = delta if direction == "downlink" else 0
                db.execute("""
                    INSERT INTO source_hourly_usage
                    (period_id,hour_start_utc,source_id,raw_identity,
                     account_id,upload_bytes,download_bytes)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(
                        period_id,hour_start_utc,source_id,raw_identity
                    ) DO UPDATE SET
                      upload_bytes=upload_bytes+excluded.upload_bytes,
                      download_bytes=download_bytes+excluded.download_bytes,
                      account_id=excluded.account_id
                """, (
                    period_id, hour, source_id, raw,
                    account_id, upload, download
                ))
            added += delta

        db.execute("""
            INSERT INTO collection_batches
            VALUES (?,?,?,?,?)
        """, (source_id, batch_id, stamp, generation, digest))
        db.commit()
        return "APPLIED", added

    except BaseException:
        db.rollback()
        raise
