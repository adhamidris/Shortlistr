"""Canonical KAP reply delivery: no streaming fragments or history replay."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from telegram_bridge import Bridge,Store
from kimi_protocol import turn_view


def message(mid,text='Same text',status='ended'):
    return {'item_id':mid,'type':'assistant_message','status':status,'text':text}


def turn(n,items,status='ended'):
    return {'turn_id':f't{n}','ordinal':n,'kind':'agent','status':status,
            'ended_at':'now' if status=='ended' else None,'items':items,'steps':[]}


class MessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();b=self.b=object.__new__(Bridge)
        b.store=Store(Path(self.tmp.name)/'state.db');b.store.put('kap-baseline:s',True)
        b.store.put('kap-message-cursor:s',{'ordinal':0,'item_id':'m0'})
        b.message_baseline_lock=asyncio.Lock();b.root=Path(self.tmp.name);b.redact=lambda x:x
        b.kimi=AsyncMock();b.say=AsyncMock()
    async def asyncTearDown(self):self.b.store.db.close();self.tmp.cleanup()

    async def test_sync_fetches_only_latest_and_cursor_and_reuses_for_relay(self):
        b=self.b;b.store.put('session','s')
        b.store.put('kap-turn-baseline:s',True);b.store.put('cursor:s','t0')
        b.pending_interactions=set();b.end_activity=AsyncMock()
        b.kimi.side_effect=[{'turns':[turn(1,[message('m1')])],'has_more':True},
                           {'turns':[turn(0,[message('m0')])],'has_more':False},
                           {'items':[]}]
        await b.sync()
        self.assertEqual(b.kimi.call_count,3)
        self.assertEqual(b.kimi.call_args_list[0].kwargs['params']['page_size'],1)
        b.say.assert_awaited_once()
        self.assertEqual(b.store.get('cursor:s'),'t1')

    async def test_relay_reuses_sync_page_without_history_request(self):
        b=self.b
        await b.relay_messages('s',[turn(0,[message('m0')]),turn(1,[message('m1')])])
        b.kimi.assert_not_called()
        b.say.assert_awaited_once()

    async def test_uncertain_reply_is_flagged_not_silently_dropped_or_repeated(self):
        b=self.b
        b.store.put('sent:kap-message:s:m1:0',{'status':'uncertain'})
        await b.relay_messages('s',[turn(0,[message('m0')]),turn(1,[message('m1')])])
        self.assertTrue(b.store.get('delivery-alert:s'))
        self.assertIn('/reply',b.say.call_args.args[1])
        self.assertEqual(b.say.call_args.args[0],'delivery-warning:kap-message:s:m1:0')
        self.assertEqual(b.store.get('kap-message-cursor:s')['item_id'],'m1')

    async def test_reply_recovery_reads_without_prompting_model(self):
        b=self.b;b.store.put('delivery-alert:s',True)
        b.kimi.return_value={'turns':[turn(1,[message('m1','Recovered response')])],'has_more':False}
        await b.command(99,'s','/reply','')
        self.assertEqual(b.say.call_args.args[1],'Recovered response')
        self.assertFalse(b.store.get('delivery-alert:s'))
        self.assertTrue(all(c.args[0]=='GET' for c in b.kimi.call_args_list))

    async def test_one_canonical_message_delivered_once_on_replay(self):
        b=self.b;b.kimi.return_value={'turns':[turn(1,[message('m1')])],'has_more':False}
        await b.relay_messages('s');await b.relay_messages('s')
        b.say.assert_awaited_once()
        self.assertEqual(b.say.call_args.args[0],'kap-message:s:m1:0')
        self.assertTrue(all('/agents/main/turns' in c.args[1] for c in b.kimi.call_args_list))

    async def test_equal_text_distinct_ids_preserved(self):
        b=self.b;b.kimi.return_value={'turns':[turn(1,[message('m1'),message('m2')])],'has_more':False}
        await b.relay_messages('s')
        self.assertEqual([c.args[0] for c in b.say.call_args_list],['kap-message:s:m1:0','kap-message:s:m2:0'])

    async def test_paginated_turns_relay_in_order(self):
        b=self.b;b.kimi.side_effect=[{'turns':[turn(2,[message('m2')]),turn(3,[message('m3')])],'has_more':True},
            {'turns':[turn(0,[message('m0')]),turn(1,[message('m1')])],'has_more':False}]
        await b.relay_messages('s')
        self.assertEqual([c.args[0] for c in b.say.call_args_list],['kap-message:s:m1:0','kap-message:s:m2:0','kap-message:s:m3:0'])
        self.assertEqual(b.kimi.call_args.kwargs['params']['before_ordinal'],2)

    async def test_streaming_message_waits_for_end(self):
        b=self.b;b.kimi.return_value={'turns':[turn(1,[message('m1','partial','running')],'running')],'has_more':False}
        await b.relay_messages('s');b.say.assert_not_called()
        self.assertEqual(b.store.get('kap-message-cursor:s')['item_id'],'m0')
        b.kimi.return_value={'turns':[turn(1,[message('m1','Finished')])],'has_more':False}
        await b.relay_messages('s')
        self.assertEqual(b.say.call_args.args[1],'Finished')

    async def test_baseline_skips_history_but_not_streaming_reply(self):
        b=self.b;b.kimi.return_value={'turns':[turn(5,[message('old'),message('new','partial','running')],'running')],'has_more':True}
        await b.baseline_messages('new-session')
        self.assertEqual(b.store.get('kap-message-cursor:new-session'),{'ordinal':5,'item_id':'old'})
        b.say.assert_not_called()

    async def test_missing_cursor_refuses_duplicate_replay(self):
        b=self.b;b.kimi.return_value={'turns':[turn(0,[message('different')])],'has_more':False}
        with self.assertRaises(ValueError):await b.relay_messages('s')
        b.say.assert_not_called()

    async def test_transcript_adapter_uses_actual_pending_interactions(self):
        b=self.b;b.kimi.side_effect=[{'turns':[turn(1,[{'type':'user_message','prompt_ids':['p1']},
            {'type':'tool_call','item_id':'c1','name':'Bash','status':'running','stage':'executing','input':{'command':'pwd'}}],'running')],'has_more':False},
            {'items':[{'interaction_id':'i1','kind':'question','request':{'questions':[]}}]}]
        d=await b.transcript('s',page_size=1)
        self.assertEqual(d['pending_interactions'],['i1'])
        self.assertEqual(d['items'][0]['triggerPromptId'],'p1')
        self.assertEqual(d['items'][0]['steps'][0]['frames'][0]['state'],'running')

    def test_preparing_tool_arguments_not_claimed_as_executing(self):
        t=turn(1,[{'type':'tool_call','item_id':'c','name':'Bash','status':'running','stage':'input'}],'running')
        self.assertEqual(turn_view(t)['steps'][0]['frames'][0]['state'],'pending')

if __name__=='__main__':unittest.main()
