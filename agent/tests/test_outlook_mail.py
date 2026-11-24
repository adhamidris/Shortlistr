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
import outlook_mail as m
import ledger
import run_report as rr


class MailTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_scan_excludes_history_and_reports_cap(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp)
            stack.enter_context(patch.object(m,'DATA',root))
            stack.enter_context(patch.object(m,'STATE',root/'state.json'))
            stack.enter_context(patch.object(m,'auth',return_value=('secret','account')))
            state=m.load_state();state['messages']['old']={'id':'old'};m.save_state(state)
            calls=[]
            async def graph(client,url,token,params=None):
                calls.append((url,params))
                if url.endswith('/inbox'):return {'id':'inbox-id'}
                if url.endswith('/junkemail'):return {'id':'junk-id'}
                if '/inbox/messages' in url:
                    return {'value':[{'id':'new','subject':'Verify'}], '@odata.nextLink':'https://graph.microsoft.com/v1.0/more'}
                return {'value':[]}
            stack.enter_context(patch.object(m,'graph_get',graph))
            result=await m.scan(root/'out.json',10)
            output=json.loads((root/'out.json').read_text(encoding='utf-8'))
            self.assertEqual([r['id'] for r in output['messages']],['new'])
            self.assertEqual(result['limited_folders'],['inbox'])
            queries=[params for url,params in calls if url.endswith('/messages')]
            self.assertEqual(len(queries),2)
            for query in queries:
                self.assertEqual(query['$top'],'25')
                self.assertEqual(query['$orderby'],'receivedDateTime desc')
                self.assertNotIn('body',query['$select'])
                self.assertTrue(query['$filter'].startswith('receivedDateTime ge '))
            self.assertIn('old',m.load_state()['messages'])

    async def test_recent_scan_cancellation_saves_partial_results(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp)
            stack.enter_context(patch.object(m,'DATA',root))
            stack.enter_context(patch.object(m,'STATE',root/'state.json'))
            stack.enter_context(patch.object(m,'auth',return_value=('secret','account')))
            async def graph(client,url,token,params=None):
                if url.endswith('/inbox'):return {'id':'inbox-id'}
                if url.endswith('/inbox/messages'):return {'value':[{'id':'new'}]}
                raise asyncio.CancelledError()
            stack.enter_context(patch.object(m,'graph_get',graph))
            with self.assertRaises(asyncio.CancelledError):await m.scan(root/'out.json')
            output=json.loads((root/'out.json').read_text(encoding='utf-8'))
            self.assertEqual(output['summary']['status'],'interrupted')
            self.assertEqual(output['messages'][0]['id'],'new')

    async def test_invalid_window_does_not_access_mail(self):
        with patch.object(m,'auth') as auth:
            for value in [0,121,True,'30']:
                with self.assertRaises(ValueError):await m.scan(Path('unused'),value)
            auth.assert_not_called()

    async def test_body_read_restricted_to_scanned_ids(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'DATA',Path(tmp)),patch.object(m,'STATE',Path(tmp)/'state.json'),patch.object(m,'auth') as auth:
            with self.assertRaises(ValueError):await m.read_selected(['unknown'])
            auth.assert_not_called()
    async def test_graph_refuses_external_continuation(self):
        async with httpx.AsyncClient() as client:
            with self.assertRaises(ValueError):await m.graph_get(client,'https://evil.example/v1.0/mail','secret')
    async def test_throttle_retry_then_success(self):
        requests=[]
        def handle(req):
            requests.append(req)
            return httpx.Response(429,headers={'Retry-After':'1'}) if len(requests)==1 else httpx.Response(200,json={'value':[]})
        with patch('asyncio.sleep',new=AsyncMock()) as sleep:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.assertEqual(await m.graph_get(client,'me/messages','secret'),{'value':[]})
            sleep.assert_awaited_once_with(1)
        self.assertTrue(all(r.method=='GET' for r in requests))
    async def test_body_not_requested_after_message_moves_outside_allowed_folders(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);stack.enter_context(patch.object(m,'DATA',root));stack.enter_context(patch.object(m,'STATE',root/'state.json'))
            stack.enter_context(patch.object(m,'auth',return_value=('secret','account')))
            state=m.load_state();state.update(account='account',folders={'inbox':{'id':'inbox'}},messages={'m':{'id':'m'}});m.save_state(state)
            get=AsyncMock(return_value={'id':'m','parentFolderId':'archive'})
            stack.enter_context(patch.object(m,'graph_get',get))
            result=await m.read_selected(['m'])
            self.assertEqual(result['messages'][0]['status'],'outside_allowed_folders')
            self.assertEqual(get.await_count,1)
            self.assertNotIn('body',get.call_args.args[3]['$select'])

    async def test_long_retry_after_is_not_shortened(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(429,headers={'Retry-After':'600'}))) as client:
            with self.assertRaisesRegex(ValueError,'600-second pause'):
                await m.graph_get(client,'me/messages','secret')

    def test_mail_cleanup_keeps_job_identity_and_removes_tracking(self):
        text=m.body_text({'contentType':'text','content':'Viewed \u034f\u200c\n\n\nhttps://example.com/jobs/123?otpToken=secret&jobId=123&trk=long'})
        self.assertNotIn('secret',text);self.assertNotIn('trk=',text)
        self.assertIn('jobId=123',text);self.assertNotIn('\u034f',text)

    def test_verification_links_exact_host_and_https(self):
        body={'contentType':'html','content':'<a href="https://auth.example.com/verify?token=secret&amp;id=1">Verify</a><a href="https://auth.example.com.evil/verify?token=bad">Wrong</a><a href="http://auth.example.com/verify">HTTP</a><a href="https://attacker@auth.example.com/verify">Userinfo</a>'}
        self.assertEqual(m.verification_links(body,'auth.example.com'),['https://auth.example.com/verify?token=secret&id=1'])

    async def test_verification_read_does_not_persist_sensitive_body(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);stack.enter_context(patch.object(m,'DATA',root));stack.enter_context(patch.object(m,'STATE',root/'state.json'))
            stack.enter_context(patch.object(m,'auth',return_value=('secret','account')))
            state=m.load_state();state.update(account='account',folders={'inbox':{'id':'inbox'}},messages={'m':{'id':'m'}});m.save_state(state)
            get=AsyncMock(side_effect=[{'id':'m','parentFolderId':'inbox'},{'id':'m','body':{'contentType':'text','content':'Code 123456. https://auth.example.com/verify?token=sensitive'}}])
            stack.enter_context(patch.object(m,'graph_get',get))
            result=await m.read_selected(['m'],'auth.example.com')
            self.assertEqual(result['messages'][0]['verification_links'],['https://auth.example.com/verify?token=sensitive'])
            self.assertIn('123456',result['messages'][0]['text'])
            self.assertFalse((root/'bodies').exists())
            self.assertNotIn('sensitive',(root/'state.json').read_text(encoding='utf-8'))

    def test_html_sanitized_without_script_or_images(self):
        self.assertEqual(m.body_text({'contentType':'html','content':'<p>Interview</p><script>steal()</script><img src="https://evil"/>'}),'Interview')

if __name__=='__main__':unittest.main()
