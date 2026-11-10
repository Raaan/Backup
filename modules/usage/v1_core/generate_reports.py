# OUTPUT_V1_COMPAT_1
#!/usr/bin/env python3
"""Offline OUTPUT V1 previews. No sending, no production writes."""
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

B = Path("/opt/la-portal")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reporting import Reporting

DATABASE = Path("/var/lib/xray-usage-v1/usage.db")
OUT = B / "output-friday-v1"

BJ = timezone(timedelta(hours=8))

def volume(value, unit):
    if unit not in ('GB', 'GiB'):
        raise ValueError('Unknown traffic unit')
    return int(value) / (1000 ** 3 if unit == 'GB' else 1024 ** 3)


def display_volume(value, unit):
    amount = volume(value, unit)
    if 0 < amount < 0.000001:
        return f"<0.000001 {unit} ({int(value)} B)"
    return f"{amount:.6f} {unit}"

def beijing(value):
    return datetime.fromisoformat(value).astimezone(BJ).strftime(
        "%Y-%m-%d %H:%M"
    )

def totals(accounts):
    return (
        sum(int(a["upload_bytes"]) for a in accounts),
        sum(int(a["download_bytes"]) for a in accounts),
        sum(int(a["total_bytes"]) for a in accounts)
    )

def pivot_hourly(rows):
    pivot = defaultdict(lambda: defaultdict(int))
    for row in rows:
        pivot[row["hour_start_utc"]][row["canonical_name"]] += (
            int(row["total_bytes"])
        )
    return pivot

def workbook(path, summary_title, chart_title, accounts,
             hours, hourly_rows, monthly, extra_bytes=0,
             weekly_window=None):
    unit = monthly["quota_unit"]
    gb = lambda value: volume(value, unit)
    wb = Workbook()
    ws = wb.active
    ws.title = summary_title

    headers = [
        "User", f"Upload {unit}", f"Download {unit}", f"Total {unit}",
        "Upload Bytes", "Download Bytes", "Total Bytes"
    ]
    ws.append(headers)

    for a in accounts:
        ws.append([
            a["canonical_name"],
            gb(a["upload_bytes"]),
            gb(a["download_bytes"]),
            gb(a["total_bytes"]),
            int(a["upload_bytes"]),
            int(a["download_bytes"]),
            int(a["total_bytes"])
        ])

    up, down, allocated = totals(accounts)

    if extra_bytes:
        ws.append([
            "Unallocated", None, None, gb(extra_bytes),
            None, None, extra_bytes
        ])

    ws.append([
        "TOTAL", gb(up), gb(down),
        gb(allocated + extra_bytes),
        up, down, allocated + extra_bytes
    ])

    chart = BarChart()
    chart.title = chart_title
    chart.y_axis.title = unit
    chart.x_axis.title = "User"
    chart.add_data(
        Reference(
            ws, min_col=4, min_row=1,
            max_row=len(accounts)+1
        ),
        titles_from_data=True
    )
    chart.set_categories(
        Reference(
            ws, min_col=1, min_row=2,
            max_row=len(accounts)+1
        )
    )
    chart.width = 18
    chart.height = 9
    ws.add_chart(chart, "I2")

    wh = wb.create_sheet("Hourly Usage")
    names = [a["canonical_name"] for a in accounts]
    wh.append(["Beijing Time"] + names + [f"Total {unit}"])

    by_hour = pivot_hourly(hourly_rows)

    for hour in hours:
        amounts = [
            by_hour[hour].get(name, 0) for name in names
        ]
        wh.append([
            beijing(hour),
            *[gb(value) for value in amounts],
            gb(sum(amounts))
        ])

    if hours:
        line = LineChart()
        line.title = (
            "Hourly Traffic - Last 7 Days"
            if weekly_window else
            "Hourly Traffic - Billing Period"
        )
        line.y_axis.title = unit
        line.x_axis.title = "Beijing Time"
        line.add_data(
            Reference(
                wh, min_col=2, max_col=1+len(names),
                min_row=1, max_row=1+len(hours)
            ),
            titles_from_data=True
        )
        line.set_categories(
            Reference(
                wh, min_col=1, min_row=2,
                max_row=1+len(hours)
            )
        )
        line.width = 21
        line.height = 10
        wh.add_chart(
            line, f"{get_column_letter(len(names)+4)}2"
        )

    wm = wb.create_sheet("Monthly Status")
    wm.append(["Metric", "Value"])
    wm.append(["Period start", monthly["period_id"]])
    wm.append(["Period end (snapshot UTC)", monthly["report_snapshot_utc"]])
    wm.append(["Billing period", monthly["period_id"]])
    wm.append([f"Allocated {unit}", gb(monthly["allocated_bytes"])])
    wm.append([
        f"Historical baseline {unit}",
        gb(monthly["unallocated"]["historical_baseline_bytes"])
    ])
    wm.append([
        f"Pending identity {unit}",
        gb(monthly["unallocated"]["pending_identity_bytes"])
    ])
    wm.append([
        f"Monthly user traffic total {unit}",
        gb(monthly["monthly_total_bytes"])
    ])
    wm.append([
        "Estimated VPS bandwidth x"+monthly["billing_factor"]+" "+unit,
        gb(monthly["estimated_vps_bytes"])
    ])
    wm.append([f"Quota {unit}", gb(monthly["quota_bytes"])])
    wm.append([f"Remaining {unit}", gb(monthly["remaining_bytes"])])
    wm.append([f"Current persisted traffic {unit}", gb(monthly["allocated_bytes"] + monthly["unallocated"]["pending_identity_bytes"])])
    wm.append(["Usage percent", round(monthly["estimated_vps_bytes"] * 100 / monthly["quota_bytes"], 3)])

    if weekly_window:
        wm.append(["Weekly start UTC", weekly_window["start_utc"]])
        wm.append([
            "Weekly end UTC (exclusive)",
            weekly_window["end_utc_exclusive"]
        ])

    for sheet in wb:
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        sheet.column_dimensions["A"].width = 29
        for idx in range(2, min(sheet.max_column+1, 18)):
            sheet.column_dimensions[
                get_column_letter(idx)
            ].width = 19

    # Nine decimals retain byte-scale traffic; raw byte columns remain exact.
    for row in ws.iter_rows(min_row=2, min_col=2, max_col=4):
        for cell in row:
            cell.number_format = '0.000000000'
    for row in wh.iter_rows(min_row=2, min_col=2):
        for cell in row:
            cell.number_format = '0.000000000'
    wb.save(path)

def make_csv(path, accounts, extra_bytes=0, unit="GiB"):
    gb = lambda value: volume(value, unit)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "User", "Upload Bytes", "Download Bytes",
            "Total Bytes", f"Total {unit}"
        ])
        for a in accounts:
            w.writerow([
                a["canonical_name"],
                a["upload_bytes"],
                a["download_bytes"],
                a["total_bytes"],
                gb(a["total_bytes"])
            ])
        if extra_bytes:
            w.writerow([
                "Unallocated", "", "",
                extra_bytes, gb(extra_bytes)
            ])
        up, down, total = totals(accounts)
        w.writerow([
            "TOTAL", up, down,
            total + extra_bytes,
            gb(total + extra_bytes)
        ])

def email_body(week_accounts, week, month, weekly_total, week_pending):
    unit = month["quota_unit"]
    lines = [
        "Xray Weekly Usage Report - OFFLINE PREVIEW",
        "",
        f"Start UTC: {week['start_utc']}",
        f"End UTC (exclusive): {week['end_utc_exclusive']}",
        "",
        "Weekly traffic summary:",
        ""
    ]
    for account in week_accounts:
        lines.append(
            f"{account['canonical_name']:<12} "
            f"{display_volume(account['total_bytes'], unit)}"
        )
    lines += [
        "",
        f"Weekly allocated total: {display_volume(weekly_total, unit)}",
        f"Weekly pending: {display_volume(week_pending, unit)}",
        "",
        f"Billing period: {month['period_id']}",
        f"Monthly user traffic: "
        f"{display_volume(month['monthly_total_bytes'], unit)}",
        f"Estimated VPS bandwidth x{month['billing_factor']}: "
        f"{display_volume(month['estimated_vps_bytes'], unit)}",
        f"Remaining quota: "
        f"{display_volume(month['remaining_bytes'], unit)}",
        "",
        "PREVIEW ONLY - NOT SENT"
    ]

    return "\n".join(lines)


def main():
    OUT.mkdir(mode=0o700, exist_ok=True)
    before = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    r = Reporting(DATABASE)
    try:
        latest = r.db.execute("""
            SELECT MAX(observed_at_utc) FROM collection_batches
        """).fetchone()[0]
        if not latest:
            raise RuntimeError("Missing shadow collection timestamp")

        # Fixed Friday 09:00 Beijing reporting boundary.
        now_bj = datetime.now(BJ)
        end_bj = now_bj.replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        end_bj -= timedelta(days=(end_bj.weekday()-4) % 7)
        if end_bj > now_bj:
            end_bj -= timedelta(days=7)

        cutoff = end_bj.astimezone(timezone.utc)
        if datetime.fromisoformat(latest) < cutoff:
            raise RuntimeError("Database has not reached Friday cutoff")

        week = r.weekly_window(cutoff.isoformat())
        observed_hours = {
            x["hour_start_utc"] for x in week["hours"]
        }
        print("WEEKLY_ZERO_FILLED_HOURS:", 168-len(observed_hours))

        print("WEEKLY_START_BEIJING:",
              (end_bj-timedelta(days=7)).isoformat())
        print("WEEKLY_END_BEIJING:", end_bj.isoformat())
        month = r.monthly_report()
        month["report_snapshot_utc"] = latest

        week_accounts = week["accounts"]
        month_accounts = month["accounts"]
        month_hours = r.hourly(month["period_id"])

        week_pending = (
            week["pending"]["upload_bytes"] +
            week["pending"]["download_bytes"]
        )
        monthly_extra = (
            month["unallocated"]["historical_baseline_bytes"] +
            month["unallocated"]["pending_identity_bytes"]
        )

        start = datetime.fromisoformat(week["start_utc"])
        weekly_hours = [
            (start + timedelta(hours=i)).isoformat()
            for i in range(168)
        ]
        monthly_hours = sorted({
            row["hour_start_utc"] for row in month_hours
        })

        files = {
            "weekly_xlsx": OUT / "weekly_preview.xlsx",
            "monthly_xlsx": OUT / "monthly_preview.xlsx",
            "weekly_csv": OUT / "weekly_preview.csv",
            "monthly_csv": OUT / "monthly_preview.csv",
            "email": OUT / "weekly_email_preview.eml"
        }

        workbook(
            files["weekly_xlsx"], "Weekly Summary",
            "7-Day Traffic by User", week_accounts,
            weekly_hours, week["hours"], month,
            week_pending, weekly_window=week
        )
        workbook(
            files["monthly_xlsx"], "Monthly Summary",
            "Traffic by User - Billing Period",
            month_accounts, monthly_hours,
            month_hours, month, monthly_extra
        )

        make_csv(
            files["weekly_csv"], week_accounts, week_pending, unit=month["quota_unit"]
        )
        make_csv(
            files["monthly_csv"], month_accounts, monthly_extra, unit=month["quota_unit"]
        )

        weekly_up, weekly_down, weekly_total = totals(
            week_accounts
        )
        msg = EmailMessage()
        msg["Subject"] = "PREVIEW - Xray Weekly Usage Report"
        msg.set_content(email_body(week_accounts, week, month, weekly_total, week_pending))
        msg.add_attachment(
            files["weekly_xlsx"].read_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=files["weekly_xlsx"].name
        )
        files["email"].write_bytes(msg.as_bytes())

        # Verify exported totals and workbook structure.
        assert len(weekly_hours) == 168
        assert sum(x["total_bytes"] for x in week["hours"]) == weekly_total
        assert sum(x["total_bytes"] for x in month_accounts) + monthly_extra == (
            month["monthly_total_bytes"]
        )

        for key, sheetnames in (
            ("weekly_xlsx", [
                "Weekly Summary", "Hourly Usage", "Monthly Status"
            ]),
            ("monthly_xlsx", [
                "Monthly Summary", "Hourly Usage", "Monthly Status"
            ])
        ):
            wb = load_workbook(files[key])
            assert wb.sheetnames == sheetnames
            assert len(wb.worksheets[0]._charts) == 1
            assert len(wb.worksheets[1]._charts) == 1
            wb.close()

        for key, expected in (
            ("weekly_csv", weekly_total + week_pending),
            ("monthly_csv", month["monthly_total_bytes"])
        ):
            with files[key].open(
                encoding="utf-8-sig", newline=""
            ) as f:
                rows = list(csv.DictReader(f))
            total_rows = [x for x in rows if x["User"] == "TOTAL"]
            assert len(total_rows) == 1
            assert int(total_rows[0]["Total Bytes"]) == expected

        assert hashlib.sha256(DATABASE.read_bytes()).hexdigest() == before

        print("===== OUTPUT V1 PREVIEW =====")
        print("Source cutoff UTC:", latest)
        print("Weekly window:", week["start_utc"],
              "to", week["end_utc_exclusive"])
        print("Weekly hours:", len(weekly_hours))
        print("Weekly allocated bytes:", weekly_total)
        print("Monthly total bytes:", month["monthly_total_bytes"])
        print("Account count:", len(month_accounts))
        print("Excel worksheets: 3 + 3")
        print("Excel charts: 2 + 2")
        print("Email sent: NO")
        print("Production modified: NO")

        print("\n===== PREVIEW FILES =====")
        for key, path in files.items():
            print(key, ":", path, "(", path.stat().st_size, "bytes)")

        print("\nOUTPUT_V1_PREVIEW_PASSED")
    finally:
        r.close()

if __name__ == "__main__":
    main()
