"""V1 authenticated ingestion boundary. No network listener."""
from datetime import datetime, timezone

from counter_engine import ingest
from remote_protocol import verify


def accept(db, body, headers, credentials, source_policy, now=None):
    # Authentication and full payload validation happen first.
    data = verify(body, headers, credentials, now=now)
    source_id = data["source_id"]

    # Mapping policy comes from trusted server configuration.
    prefixes = source_policy.get(source_id)
    if not isinstance(prefixes, (list, tuple)):
        raise ValueError("No trusted source policy")

    observed = datetime.fromisoformat(
        data["observed_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)

    # The server, not the sender, chooses the billing period.
    periods = db.execute("""
        SELECT period_id,started_at_utc,ended_at_utc
        FROM billing_periods
    """).fetchall()

    active = []
    for period_id, start, end in periods:
        start_dt = datetime.fromisoformat(start).astimezone(timezone.utc)
        end_dt = (
            datetime.fromisoformat(end).astimezone(timezone.utc)
            if end else None
        )
        if start_dt <= observed and (
            end_dt is None or observed < end_dt
        ):
            active.append(period_id)

    if len(active) != 1:
        raise ValueError("Ambiguous billing period")

    counters = [
        (
            item["raw_identity"],
            item["direction"],
            item["cumulative_bytes"]
        )
        for item in data["counters"]
    ]

    status, added = ingest(
        db=db,
        source_id=source_id,
        batch_id=data["batch_id"],
        generation=data["generation_id"],
        observed_at=data["observed_at"],
        counters=counters,
        period_id=active[0],
        trusted_prefixes=prefixes
    )

    return {
        "status": status,
        "accepted_bytes": added,
        "source_id": source_id,
        "batch_id": data["batch_id"]
    }
