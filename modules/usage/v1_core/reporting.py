"""V1 report data contract. Read-only SQLite access."""
import sqlite3
from billing import load, normalize, quota_bytes, estimated_bytes
from pathlib import Path
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))

class Reporting:
    def __init__(self, database, billing=None):
        self.billing = normalize(billing) if billing is not None else load()
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        self.db = sqlite3.connect(uri, uri=True)
        self.db.row_factory = sqlite3.Row

    def rows(self, sql, params=()):
        return [
            dict(row)
            for row in self.db.execute(sql, params)
        ]

    def active_period(self):
        rows = self.rows("""
            SELECT period_id FROM billing_periods
            WHERE ended_at_utc IS NULL
        """)
        if len(rows) != 1:
            raise ValueError("Expected one active period")
        return rows[0]["period_id"]

    def accounts(self, period):
        return self.rows("""
            SELECT account_id,canonical_name,
                   upload_bytes,download_bytes,total_bytes
            FROM v1_account_period
            WHERE period_id=?
            ORDER BY canonical_name COLLATE NOCASE
        """, (period,))

    def hourly(self, period):
        data = self.rows("""
            SELECT h.hour_start_utc,a.canonical_name,
                   h.upload_bytes,h.download_bytes,
                   h.total_bytes
            FROM v1_account_hourly h
            JOIN accounts a ON a.account_id=h.account_id
            WHERE h.period_id=?
            ORDER BY h.hour_start_utc,a.canonical_name
        """, (period,))
        for row in data:
            row["beijing_hour"] = (
                datetime.fromisoformat(
                    row["hour_start_utc"]
                ).astimezone(BEIJING).isoformat()
            )
        return data

    def sources(self, period):
        return self.rows("""
            SELECT source_id,raw_identity,
                   canonical_name,mapping_status,
                   hour_start_utc,
                   upload_bytes,download_bytes,total_bytes
            FROM v1_source_hourly
            WHERE period_id=?
            ORDER BY hour_start_utc,source_id,raw_identity
        """, (period,))

    def unallocated(self, period):
        baseline = self.db.execute("""
            SELECT total_bytes FROM v1_period_unallocated
            WHERE period_id=?
        """, (period,)).fetchone()

        pending = self.db.execute("""
            SELECT COALESCE(SUM(total_bytes),0)
            FROM v1_unmapped_hourly
            WHERE period_id=?
        """, (period,)).fetchone()[0]

        return {
            "historical_baseline_bytes":
                int(baseline[0]) if baseline else 0,
            "pending_identity_bytes": int(pending)
        }


    def weekly_window(self, as_of):
        """Last 168 completed UTC hours, across billing periods."""
        end = datetime.fromisoformat(
            as_of.replace("Z", "+00:00")
        )
        if end.tzinfo is None:
            raise ValueError("Timezone required")
        end = end.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        start = end - timedelta(hours=168)
        begin_s, end_s = start.isoformat(), end.isoformat()

        accounts = self.rows("""
            SELECT a.account_id,a.canonical_name,
                   COALESCE(SUM(h.upload_bytes),0) AS upload_bytes,
                   COALESCE(SUM(h.download_bytes),0) AS download_bytes,
                   COALESCE(SUM(h.total_bytes),0) AS total_bytes
            FROM accounts a
            LEFT JOIN v1_account_hourly_all h
              ON h.account_id=a.account_id
             AND julianday(h.hour_start_utc)>=julianday(?)
             AND julianday(h.hour_start_utc)<julianday(?)
            GROUP BY a.account_id,a.canonical_name
            ORDER BY a.canonical_name COLLATE NOCASE
        """, (begin_s, end_s))

        hours = self.rows("""
            SELECT h.hour_start_utc,a.canonical_name,
                   h.upload_bytes,h.download_bytes,h.total_bytes
            FROM v1_account_hourly_all h
            JOIN accounts a ON a.account_id=h.account_id
            WHERE julianday(h.hour_start_utc)>=julianday(?)
              AND julianday(h.hour_start_utc)<julianday(?)
            ORDER BY h.hour_start_utc,a.canonical_name
        """, (begin_s, end_s))

        for row in hours:
            row["beijing_hour"] = datetime.fromisoformat(
                row["hour_start_utc"]
            ).astimezone(BEIJING).isoformat()

        pending = self.rows("""
            SELECT COALESCE(SUM(upload_bytes),0) AS upload_bytes,
                   COALESCE(SUM(download_bytes),0) AS download_bytes
            FROM v1_unmapped_hourly
            WHERE julianday(hour_start_utc)>=julianday(?)
              AND julianday(hour_start_utc)<julianday(?)
        """, (begin_s, end_s))[0]

        return {
            "start_utc": begin_s,
            "end_utc_exclusive": end_s,
            "accounts": accounts,
            "hours": hours,
            "pending": pending
        }

    def monthly_report(self):
        period = self.active_period()
        accounts = self.accounts(period)
        unallocated = self.unallocated(period)
        allocated = sum(x["total_bytes"] for x in accounts)
        total = (
            allocated
            + unallocated["historical_baseline_bytes"]
            + unallocated["pending_identity_bytes"]
        )
        quota = quota_bytes(self.billing)
        estimated = estimated_bytes(self.billing,total)
        return {
            "period_id": period,
            "accounts": accounts,
            "unallocated": unallocated,
            "allocated_bytes": allocated,
            "monthly_total_bytes": total,
            "estimated_vps_bytes": estimated,
            "billing_factor": self.billing["factor"],
            "quota_amount": self.billing["quota"],
            "quota_unit": self.billing["unit"],
            "quota_bytes": quota,
            "remaining_bytes": max(quota - estimated, 0)
        }

    def close(self):
        self.db.close()
