import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import httpx
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import followup as f
import outlook_mail as m
import ledger
import run_report as rr


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.stack=ExitStack()
        self.stack.enter_context(patch.object(f,'ROOT',self.root))
        self.stack.enter_context(patch.object(m,'DATA',self.root/'data/followup'))
        self.stack.enter_context(patch.object(m,'STATE',m.DATA/'mail-state.json'))
        self.stack.enter_context(patch.object(ledger,'LEDGER',self.root/'ledger/applications.jsonl'))
        self.run=self.root/'runs/check'
        self.application={'event':'applied','platform':'linkedin','job_id':'1','company':'A','title':'Engineer','ts':'2026-10-01T00:00:00+00:00'}
        ledger.append_events([self.application])
    def tearDown(self):self.stack.close();self.tmp.cleanup()
    def message(self,id):
        state=m.load_state();state['messages'][id]={'id':id,'received_at':'2026-10-10T00:00:00+00:00'};m.save_state(state)
        import hashlib
        rr.atomic_json(m.DATA/'bodies'/(hashlib.sha256(id.encode()).hexdigest()+'.json'),{'text':'evidence'})
    def test_idempotent_updates_and_acknowledgement(self):
        self.message('m1');u={'job_id':'1','status':'rejected','reason':'Employer rejection','message_id':'m1'}
        self.assertEqual(f.record(self.run,[u])['new_events'],1)
        self.assertEqual(f.record(self.run,[u])['new_events'],0)
        self.assertEqual(m.load_state()['processed']['m1']['status'],'recorded')
        self.assertEqual(f.current_rows(ledger.read_events())[0]['status'],'rejected')
    def test_late_confirmation_and_activity_do_not_regress_outcome(self):
        events=[self.application,dict(self.application,event='rejected',ts='2026-10-02T00:00:00Z'),dict(self.application,event='confirmation',ts='2026-10-03T00:00:00Z'),dict(self.application,event='resume_downloaded',ts='2026-10-04T00:00:00Z')]
        self.assertEqual(f.current_rows(events)[0]['status'],'rejected')
    def test_unmatched_job_and_unread_message_fail_without_ack(self):
        state=m.load_state();state['messages']['x']={'id':'x'};m.save_state(state)
        with self.assertRaises(ValueError):f.record(self.run,[{'job_id':'2','status':'interview','reason':'invite','message_id':'x'}])
        with self.assertRaises(ValueError):f.record(self.run,[{'job_id':'1','status':'interview','reason':'invite','message_id':'x'}])
        self.assertEqual(m.load_state()['processed'],{})
    def test_ignore_metadata_without_body(self):
        state=m.load_state();state['messages']['x']={'id':'x'};m.save_state(state)
        f.record(self.run,[],['x']);self.assertEqual(m.load_state()['processed']['x']['status'],'ignored')
    def test_csv_formula_is_inert(self):
        ledger.append_events([dict(self.application,job_id='2',company='=HYPERLINK("evil")')])
        f.render(self.run)
        self.assertIn("'=HYPERLINK",(self.root/'data/applied-jobs.csv').read_text(encoding='utf-8'))



class ScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_pending_and_cursor_resume(self):
        import application_mail as scanner
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp)
            stack.enter_context(patch.object(m,'DATA',root))
            stack.enter_context(patch.object(m,'STATE',root/'state.json'))
            stack.enter_context(patch.object(m,'auth',return_value=('secret','account')))
            stack.enter_context(patch.object(ledger,'read_events',return_value=[{'event':'applied','ts':'2026-10-01T00:00:00Z'}]))
            calls=[]
            async def graph(client,url,token,params=None):
                calls.append(url)
                if url.endswith('/inbox'):return {'id':'inbox'}
                if url.endswith('/junkemail'):return {'id':'junk'}
                if 'inbox/messages/delta?' in url:
                    return {'value':[{'id':'one','subject':'Confirmation','receivedDateTime':'2026-10-02T00:00:00Z'}], '@odata.nextLink':'https://graph.microsoft.com/v1.0/next'}
                if url.endswith('/next'):raise asyncio.CancelledError()
                return {'value':[], '@odata.deltaLink':'https://graph.microsoft.com/v1.0/saved'}
            stack.enter_context(patch.object(m,'graph_get',graph))
            with self.assertRaises(asyncio.CancelledError):await scanner.scan(root/'mail.json')
            self.assertEqual(m.load_state()['folders']['inbox']['cursor'],'https://graph.microsoft.com/v1.0/next')
            self.assertEqual(json.loads((root/'mail.json').read_text(encoding='utf-8'))['summary']['status'],'interrupted')
            calls.clear()
            async def finish(client,url,token,params=None):
                calls.append(url)
                if url.endswith('/junkemail'):return {'id':'junk'}
                return {'value':[], '@odata.deltaLink':'https://graph.microsoft.com/v1.0/saved'}
            with patch.object(m,'graph_get',finish):result=await scanner.scan(root/'mail.json')
            self.assertEqual(calls[0],'https://graph.microsoft.com/v1.0/next')
            self.assertEqual(result['pending_messages'],1)
            self.assertEqual(result['status'],'complete')
