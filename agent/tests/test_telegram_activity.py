import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from telegram_activity import snapshot, tool_label, activity_html
from telegram_bridge import Bridge, Store
from kimi_protocol import LiveActivity

class ActivityFormatTests(unittest.TestCase):
    def test_safe_arguments_only(self):
        frame = {'name': 'Bash', 'input': {'command': 'python agent/scripts/ledger.py --password SECRET'}}
        self.assertEqual(tool_label(frame), '💻 Running script\nledger.py')
        self.assertEqual(tool_label({'name': 'Bash', 'input': {'command': 'TOKEN=SECRET curl https://x'}}), '💻 Shell command')
        label = tool_label({'name': 'mcp__playwright__browser_navigate', 'input': {'url': 'https://user:SECRET@example.com/path?token=SECRET'}})
        self.assertEqual(label, '🌐 Opening page\nexample.com')
        self.assertEqual(tool_label({'name':'mcp__playwright__browser_run_code_unsafe', 'input': {'code':'SECRET'}}), '🔧 Running browser code')

    def test_factual_search_labels_and_html_escaping(self):
        label = tool_label({'name':'mcp__linkedin__search_jobs', 'input':{'keyword':'AI <Engineer>', 'location':'Egypt', 'posted_within':'week', 'secret':'HIDDEN'}})
        html = activity_html({'calls':[{'label':label,'state':'running'}]}, connected=True,running=True,waiting=False)
        self.assertIn('AI &lt;Engineer&gt;', html)
        self.assertNotIn('HIDDEN',html)

    def test_only_running_calls_and_latest_frame_are_shown(self):
        frames=[{'kind':'tool','toolCallId':'a','name':'Bash','state':'running','input':{'command':'echo SECRET'}},
                {'kind':'tool','toolCallId':'a','name':'Bash','state':'done','input':{'command':'echo SECRET'}},
                {'kind':'tool','toolCallId':'b','name':'Bash','state':'pending','input':{'command':'echo SECRET'}}]
        d=snapshot('s',{'turnId':'t1','steps':[{'frames':frames}]})
        self.assertEqual(len(d['calls']),1)
        self.assertEqual(d['calls'][0]['state'],'done')
        self.assertIn('Done',activity_html(d,connected=True,running=True,waiting=False))
        self.assertIsNone(activity_html({'calls':[]},connected=True,running=True,waiting=False))

    def test_long_keyword_wraps_without_old_45_character_cutoff(self):
        keyword='AI Solutions and Agentic Automation Engineer for Business Operations'
        label=tool_label({'name':'mcp__remotive__search_jobs','input':{'keyword':keyword}})
        self.assertIn(keyword,label)
        self.assertIn('Remotive',label)

    def test_shell_cd_and_known_script_action(self):
        label=tool_label({'name':'Bash','input':{'command':'cd /some/repo && python3 agent/scripts/run_report.py record --secret SECRET'}})
        self.assertEqual(label,'📝 Recording job outcomes\nrun_report.py')
        self.assertNotIn('SECRET',label)

    def test_application_labels_and_unknown_tool_are_factual(self):
        self.assertEqual(tool_label({'name':'mcp__greenhouse__apply_job','input':{'action':'fill'}}),'✍️ Filling Greenhouse application')
        self.assertEqual(tool_label({'name':'mcp__unknown__new_tool','input':{'secret':'SECRET'}}),'🔧 mcp__unknown__new_tool')

class ActivityBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_activity_signal_filters_session_and_text_deltas(self):
        b = self.b
        b.activity_wake = asyncio.Event()
        for event in [
            {'session_id': 'other', 'agent_id': 'main', 'type': 'item.started'},
            {'session_id': 's', 'agent_id': 'child', 'type': 'item.started'},
            {'session_id': 's', 'agent_id': 'main', 'type': 'item.delta'},
        ]:
            b.signal_activity(event)
            self.assertFalse(b.activity_wake.is_set())
        for kind in ['item.started', 'item.updated', 'item.ended', 'turn.ended']:
            b.signal_activity({'session_id': 's', 'agent_id': 'main', 'type': kind})
            self.assertTrue(b.activity_wake.is_set())
            b.activity_wake.clear()

    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.b=object.__new__(Bridge)
        b=self.b
        b.store=Store(Path(self.tmp.name)/'state.db');b.store.put('session','s')
        b.connected=True;b.chat=42;b.live={'session_id':'s','status':'running'}
        b.pending_interactions=set();b.activity_snapshot={'session':'s','calls':[]}
        b.activity_lock=asyncio.Lock();b.activity_text=None;b.live_activity=LiveActivity()
        b.redact=lambda text:text;b.telegram=AsyncMock();b.kimi=AsyncMock();b.transcript=AsyncMock()
        async def say(key,text,**kwargs):
            b.store.put('sent:'+key,{'status':'sent','message_id':123})
            return 123
        b.say=AsyncMock(side_effect=say)
    async def asyncTearDown(self):
        self.b.store.db.close();self.tmp.cleanup()
    async def test_off_has_no_network_or_output(self):
        await self.b.update_activity()
        self.b.kimi.assert_not_called();self.b.telegram.assert_not_called();self.b.say.assert_not_called()
    def active_turn(self):
        return {'turn_id':'t1','ordinal':1,'status':'running','items':[{'type':'user_message','item_id':'u1','prompt_ids':['p1']},{'type':'tool_call','item_id':'c1','name':'Read','status':'running','stage':'executing'}]}
    async def test_enabling_only_sets_preference_without_card(self):
        b=self.b
        await b.command(1,'s','/activity','on')
        self.assertTrue(b.store.get('activity')['enabled'])
        self.assertIsNone(b.store.get('activity-card'))
        b.kimi.assert_not_called()
        self.assertEqual(b.say.call_args.args[0],'command:1')
    async def test_card_created_after_prompt_then_deleted_on_completion(self):
        b=self.b;b.store.put('activity',{'enabled':True});b.live_activity.apply({'type':'turns','turns':[self.active_turn()]})
        await b.begin_activity('s',2,'p1')
        await b.update_activity();await b.update_activity()
        b.say.assert_awaited_once()
        b.telegram.assert_not_called()  # Unchanged activity does not edit just for animation.
        b.live_activity.turn['status']='ended'
        await b.end_activity('s',{'turnId':'t1','triggerPromptId':'p1','endedAt':'now'})
        self.assertEqual(b.telegram.call_args.args[0],'deleteMessage')
        self.assertIsNone(b.store.get('activity-card'))
        b.telegram.reset_mock();await b.update_activity();b.telegram.assert_not_called()
    async def test_thinking_has_no_card_and_fast_completed_tool_becomes_visible(self):
        b=self.b;b.store.put('activity',{'enabled':True})
        data=self.active_turn();data['items']=[]
        b.live_activity.apply({'type':'turns','turns':[data]})
        await b.begin_activity('s',2,'p1');await b.update_activity()
        b.say.assert_not_called()
        b.live_activity.apply({'type':'item.ended','turn_id':'t1','item':{'type':'tool_call','item_id':'fast','name':'Bash','status':'ended','outcome':'completed','input':{'command':'pwd'}}})
        await b.update_activity()
        b.say.assert_awaited_once()
        self.assertIn('Done',b.say.call_args.args[1])
        self.assertIn('pwd',b.say.call_args.args[1])
        await b.update_activity();b.telegram.assert_not_called()

    async def test_steer_replaces_old_card_after_new_input(self):
        b=self.b;b.store.put('activity',{'enabled':True});b.live_activity.apply({'type':'turns','turns':[self.active_turn()]})
        await b.begin_activity('s',2,'p1');await b.update_activity()
        await b.begin_activity('s',3,'p2');await b.update_activity()
        self.assertEqual(b.say.call_count,2)
        self.assertEqual(b.telegram.call_args.args[0],'deleteMessage')
        self.assertEqual(b.store.get('activity-card')['key'],'activity-prompt:3')
        await b.begin_activity('s',3,'p2')
        self.assertEqual(b.say.call_count,2)  # Same input retry cannot recreate.
    async def test_fast_completed_turn_creates_no_card(self):
        b=self.b;b.store.put('activity',{'enabled':True})
        d=self.active_turn();d['status']='ended';b.live_activity.apply({'type':'turns','turns':[d]})
        await b.begin_activity('s',2,'p1');await b.update_activity()
        b.say.assert_not_called()
        self.assertIsNone(b.store.get('activity-card'))
    async def test_restart_cleans_up_finished_card(self):
        b=self.b;b.store.put('activity',{'enabled':True})
        b.store.put('activity-card',{'key':'activity-prompt:2','session':'s','prompt_id':'p1','turn':'t1'})
        b.store.put('sent:activity-prompt:2',{'status':'sent','message_id':123})
        d=self.active_turn();d['status']='ended';b.live_activity.apply({'type':'turns','turns':[d]})
        await b.update_activity()
        b.say.assert_not_called()
        self.assertEqual(b.telegram.call_args.args[0],'deleteMessage')
        self.assertIsNone(b.store.get('activity-card'))

    async def test_external_run_has_activity_without_telegram_prompt_or_http(self):
        b=self.b;b.store.put('activity',{'enabled':True})
        b.live_activity.apply({'type':'turns','turns':[self.active_turn()]})
        await b.update_activity()
        b.say.assert_awaited_once()
        self.assertEqual(b.store.get('activity-card')['key'],'activity-turn:s:t1')
        b.transcript.assert_not_called();b.kimi.assert_not_called()
        await b.end_activity('s',{'turnId':'older','triggerPromptId':None,'endedAt':'now'})
        self.assertIsNotNone(b.store.get('activity-card'))
        b.live_activity.apply({'type':'item.updated','turn_id':'t1','item_id':'c1',
                               'set':{'status':'ended','outcome':'completed'}})
        await b.update_activity()
        self.assertIn('Done',b.telegram.call_args.args[1]['text'])
        b.live_activity.apply({'type':'turn.ended','turn':{'turn_id':'t1','ordinal':1,'status':'ended'}})
        await b.update_activity()
        self.assertIsNone(b.store.get('activity-card'))

    def test_reconnect_snapshot_replaces_stale_items_and_ignores_deltas(self):
        state=LiveActivity()
        state.apply({'type':'turns','turns':[self.active_turn()]})
        state.apply({'type':'item.delta','turn_id':'t1','item_id':'c1','delta':'SECRET'})
        self.assertNotIn('SECRET',str(state.turn))
        state.apply({'type':'turns','turns':[{'turn_id':'t2','ordinal':2,'status':'ended','items':[]}]})
        self.assertEqual(state.turn['turn_id'],'t2')
        self.assertEqual(state.turn['items'],[])
        state.apply({'type':'turn.ended','turn':{'turn_id':'t1','ordinal':1,'status':'ended'}})
        self.assertEqual(state.turn['turn_id'],'t2')

if __name__=='__main__':unittest.main()
