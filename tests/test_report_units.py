import csv
import pathlib
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'modules/usage/v1_core'))
import generate_reports as reports
from openpyxl import load_workbook


class ReportUnitTests(unittest.TestCase):
    def test_decimal_binary_and_small_nonzero_display(self):
        self.assertEqual(reports.volume(1_000_000_000,'GB'),1)
        self.assertEqual(reports.volume(1_073_741_824,'GiB'),1)
        self.assertAlmostEqual(reports.volume(1_000_000_000,'GiB'),0.9313225746154785)
        self.assertEqual(reports.display_volume(1,'GB'),'<0.000001 GB (1 B)')
        self.assertEqual(reports.display_volume(0,'GB'),'0.000000 GB')

    def test_email_csv_workbook_and_charts_follow_configured_unit(self):
        accounts=[{'canonical_name':'Raan','upload_bytes':1_000_000_000,
                   'download_bytes':0,'total_bytes':1_000_000_000}]
        hour='2026-11-10T00:00:00+00:00'
        rows=[dict(accounts[0],hour_start_utc=hour)]
        for unit in ('GB','GiB'):
            with self.subTest(unit=unit), tempfile.TemporaryDirectory() as tmp:
                folder=pathlib.Path(tmp)
                month={'period_id':hour,'report_snapshot_utc':hour,'quota_unit':unit,
                       'allocated_bytes':1_000_000_000,'monthly_total_bytes':1_000_000_000,
                       'estimated_vps_bytes':2_000_000_000,'billing_factor':'2',
                       'quota_bytes':600_000_000_000 if unit=='GB' else 600*1024**3,
                       'unallocated':{'historical_baseline_bytes':0,'pending_identity_bytes':0}}
                month['remaining_bytes']=month['quota_bytes']-month['estimated_vps_bytes']
                week={'start_utc':hour,'end_utc_exclusive':hour}
                body=reports.email_body(accounts,week,month,1_000_000_000,0)
                self.assertIn(reports.display_volume(1_000_000_000,unit),body)
                self.assertNotIn('GiB' if unit=='GB' else ' GB',body)
                csvpath=folder/'report.csv'
                reports.make_csv(csvpath,accounts,unit=unit)
                with csvpath.open(encoding='utf-8-sig',newline='') as f:
                    data=list(csv.DictReader(f))
                self.assertEqual(int(data[0]['Total Bytes']),1_000_000_000)
                self.assertEqual(float(data[0]['Total '+unit]),reports.volume(1_000_000_000,unit))
                xlsx=folder/'report.xlsx'
                reports.workbook(xlsx,'Summary','Traffic',accounts,[hour],rows,month)
                wb=load_workbook(xlsx)
                try:
                    self.assertEqual(wb['Summary']['D1'].value,'Total '+unit)
                    self.assertEqual(wb['Summary']['D2'].value,reports.volume(1_000_000_000,unit))
                    self.assertEqual(wb['Summary']['G2'].value,1_000_000_000)
                    self.assertEqual(wb['Summary']['D2'].number_format,'0.000000000')
                    for sheet in (wb['Summary'],wb['Hourly Usage']):
                        self.assertEqual(sheet._charts[0].y_axis.title.tx.rich.p[0].r[0].t,unit)
                    metrics={r[0].value:r[1].value for r in wb['Monthly Status'].iter_rows()}
                    self.assertEqual(metrics['Quota '+unit],600)
                finally:
                    wb.close()


if __name__=='__main__':
    unittest.main()
