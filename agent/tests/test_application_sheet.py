import csv
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import ledger
import outlook_mcp


class SheetTests(unittest.TestCase):
    def test_sheet_refresh_needs_only_standard_library(self):
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        with tempfile.TemporaryDirectory() as tmp:
            code="import sys;sys.path.insert(0,sys.argv[1]);from application_sheet import refresh;refresh([dict(event='applied',platform='linkedin',job_id='1',ts='2026-11-23')],sys.argv[2])"
            result=subprocess.run([sys.executable,'-S','-c',code,str(scripts),str(Path(tmp)/'sheet.csv')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_submissions_deduplicated_and_manual_notes_preserved(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ledger, 'LEDGER', Path(tmp)/'ledger/applications.jsonl'):
            job = dict(event='applied', platform='linkedin', job_id='1', company='=bad', title='Engineer', ts='2026-10-31')
            ledger.append_events([job, dict(job, event='seen')])
            path = Path(tmp)/'data/applied-jobs.csv'
            with path.open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f); fields = reader.fieldnames; rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['company'], "'=bad")
            rows[0]['manual_notes'] = 'Call recruiter'
            rows[0]['manual_status'] = 'Interview'
            with path.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
            ledger.append_events([job, dict(job, job_id='2')])
            with path.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['manual_notes'], 'Call recruiter')
            self.assertEqual(rows[0]['manual_status'], 'Interview')


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_email_tools_are_exposed(self):
        self.assertEqual({t.name for t in await outlook_mcp.mcp.list_tools()},
                         {'scan_application_mail', 'read_application_mail', 'scan_application_updates', 'record_application_updates'})
