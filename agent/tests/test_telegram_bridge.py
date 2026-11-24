import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from telegram_bridge import Bridge, Store, APIError, authorized, safe_file, assistant_text, pending_records


def update(uid=7, text='hello', user=42, chat=42, kind='private'):
    return {'update_id': uid, 'message': {'message_id': uid, 'chat': {'id': chat, 'type': kind}, 'from': {'id': user}, 'text': text}}


class StateTests(unittest.TestCase):
    def test_authorization_checks_sender_chat_and_type(self):
        self.assertTrue(authorized(update(), 42, 42))
        for x in [update(user=9), update(chat=9), update(kind='group')]:
            self.assertFalse(authorized(x, 42, 42))
        q = {'callback_query': {'from': {'id': 9}, 'message': update()['message']}}
        self.assertFalse(authorized(q, 42, 42))

    def test_ingestion_survives_restart_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.db'
            s = Store(path)
            s.ingest(update(), 'session-A', True)
            s.ingest(update(), 'session-A', True)
            s.db.close()
            s = Store(path)
            self.assertEqual(s.pending(), 1)
            self.assertEqual(s.get('offset'), 8)
            self.assertEqual(s.next()[1], 'session-A')
            s.ingest(update(8, user=9), 'session-A', False)
            self.assertEqual(s.pending(), 1)
            self.assertEqual(s.get('offset'), 9)
            s.finish(7)
            self.assertEqual(s.pending(), 0)

    def test_deliverable_paths_reject_secret_symlink_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ((root/'report.csv').resolve()).write_text('test', encoding='utf-8')
            (root/'secrets').mkdir()
            (root/'secrets'/'token').write_text('secret', encoding='utf-8')
            (root/'innocent.txt').symlink_to(root/'secrets'/'token')
            self.assertEqual(safe_file(root, 'report.csv'), (root/'report.csv').resolve())
            for name in ['../outside', 'secrets/token', 'innocent.txt', '.git/config']:
                with self.assertRaises(ValueError):
                    safe_file(root, name)

    def test_render_never_exposes_thinking_or_tools(self):
        message = {'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'private'},
            {'type': 'tool_use', 'input': 'secret'},
            {'type': 'text', 'text': 'hello'}]}
        self.assertEqual(assistant_text(message), 'hello')
        self.assertEqual(assistant_text(dict(message, role='user')), '')

    def test_live_snapshot_pending_ids_are_resolved(self):
        snap = {'pending_interactions': ['q1'], 'interactions': [
            {'interactionId': 'q1', 'interactionKind': 'question', 'state': 'pending', 'request': {}},
            {'interactionId': 'q2', 'interactionKind': 'approval', 'state': 'ended', 'request': {}}]}
        self.assertEqual(pending_records(snap)[0]['interaction_id'], 'q1')
        self.assertEqual(len(pending_records(snap)), 1)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.b = object.__new__(Bridge)
        self.b.root = self.root
        self.b.store = Store(self.root/'bridge.db')
        self.b.store.put('session', 'session-A')
        self.b.chat = self.b.user = 42
        self.b.profile = AsyncMock(return_value={'metadata': {'cwd': str(self.root)}, 'agent_config': {'model': 'model-A'}})
        self.b.say = AsyncMock(return_value=101)
        self.b.kimi = AsyncMock(return_value={'status': 'running'})
        self.b.telegram = AsyncMock()
        self.b.wake = asyncio.Event()
        self.b.baseline_messages = AsyncMock()
        self.b.activity_lock = asyncio.Lock()

    async def asyncTearDown(self):
        self.b.store.db.close()
        self.tmp.cleanup()

    async def test_prompt_inherits_server_settings_not_cached_overrides(self):
        b=self.b
        b.store.put('settings:session-A',{'model':'stale','thinking':'max'})
        b.store.put('live-settings:session-A',{'model':'also-cached'})
        await b.handle(7,'session-A',update())
        payload=b.kimi.call_args.kwargs['json']
        self.assertNotIn('model',payload)
        self.assertNotIn('thinking',payload)
        self.assertNotIn('permission_mode',payload)

    async def test_lost_prompt_response_reuses_identical_payload(self):
        self.b.kimi.side_effect = [OSError('lost response'), {'status': 'running'}]
        with self.assertRaises(OSError):
            await self.b.handle(7, 'session-A', update())
        await self.b.handle(7, 'session-A', update())
        calls = self.b.kimi.call_args_list
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0].kwargs['json']['prompt_id'], 'telegram-42-7')

    async def test_duplicate_id_after_restart_reconciles_completed_prompt(self):
        self.b.kimi.side_effect = [APIError(40927), {'active': None, 'queued': []}]
        await self.b.handle(7, 'session-A', update())
        self.assertEqual(self.b.kimi.call_count, 2)
        self.assertEqual(self.b.kimi.call_args.args, ('GET', '/sessions/session-A/prompts'))
        self.assertTrue(self.b.wake.is_set())

    async def test_duplicate_queued_prompt_is_steered_without_resubmitting_new_id(self):
        self.b.kimi.side_effect = [APIError(40927), {'active': None, 'queued': [{'prompt_id': 'telegram-42-7', 'status': 'queued'}]}, {}]
        await self.b.handle(7, 'session-A', update())
        self.assertEqual(self.b.kimi.call_count, 3)
        self.assertEqual(self.b.kimi.call_args.args, ('POST', '/sessions/session-A/prompts:steer'))

    async def test_status_shows_usage_without_internal_ids_or_zero_counters(self):
        b = self.b
        b.connected = True
        b.store.put('usage:session-A', {'context_tokens': 65536, 'max_context_tokens': 262144})
        b.kimi.side_effect = [
            {'items': [{'model': 'model-A', 'display_name': 'Kimi test'}]},
            {'kind': 'ok', 'quota': {'usages': {'limit5h': {'usedRatio': .25}, 'monthTotal': {'usedRatio': .1}}}},
        ]
        await b.show_status(7, 'session-A')
        text = b.say.call_args.args[1]
        self.assertIn('25%', text)
        self.assertIn('75.0% left', text)
        self.assertIn('90.0% left', text)
        self.assertNotIn('session-A', text)
        self.assertNotIn('Waiting to deliver', text)
        self.assertNotIn('unconfirmed', text)
        self.assertEqual(len(b.say.call_args.args), 2)  # No unsolicited buttons.

    async def test_typing_only_for_active_connected_main_session(self):
        b = self.b
        b.connected = True
        b.pending_interactions = set()
        b.live = {'session_id': 'session-A', 'agent_id': 'main', 'status': 'running'}
        await b.typing_once()
        b.telegram.assert_awaited_once_with('sendChatAction', {'chat_id': 42, 'action': 'typing'})
        for change in ({'status': 'idle'}, {'session_id': 'other'}, {'agent_id': 'subagent'}):
            b.telegram.reset_mock()
            old = b.live.copy()
            b.live.update(change)
            await b.typing_once()
            b.telegram.assert_not_called()
            b.live = old
        b.telegram.reset_mock()
        b.pending_interactions.add('question-1')
        await b.typing_once()
        b.telegram.assert_not_called()
        b.pending_interactions.clear()
        b.connected = False
        await b.typing_once()
        b.telegram.assert_not_called()

    async def test_sync_has_no_working_or_finished_messages_but_keeps_errors(self):
        b = self.b
        b.baseline = AsyncMock()
        b.relay_messages = AsyncMock()
        b.redact = lambda text: text
        b.transcript = AsyncMock(return_value={'pending_interactions': [], 'interactions': [], 'items': [
            {'kind': 'turn', 'turnId': 't1', 'state': 'running', 'steps': []},
            {'kind': 'turn', 'turnId': 't2', 'state': 'completed', 'endedAt': 'now', 'steps': []},
            {'kind': 'turn', 'turnId': 't3', 'state': 'failed', 'endedAt': 'now', 'error': 'Service unavailable', 'steps': []}]})
        await b.sync()
        b.say.assert_awaited_once()
        self.assertIn('Service unavailable', b.say.call_args.args[1])
        self.assertEqual(b.store.get('cursor:session-A'), 't3')

    async def test_usage_is_account_only_and_has_no_buttons(self):
        self.b.kimi.return_value = {'kind': 'ok', 'quota': {'usages': {'limit5h': {'usedRatio': .25, 'resetAt': '2026-11-22T00:00:00Z'}}}}
        await self.b.show_status(7, 'session-A', detailed=True)
        self.assertEqual(self.b.kimi.call_count, 1)
        text = self.b.say.call_args.args[1]
        self.assertIn('Account usage', text)
        self.assertIn('75.0% left', text)
        self.assertNotIn('Context', text)
        self.assertNotIn('Model:', text)
        self.assertEqual(len(self.b.say.call_args.args), 2)

    async def test_delivery_menu_is_removed(self):
        with self.assertRaises(ValueError):
            await self.b.command(7, 'session-A', '/deliveries', '')
        self.b.kimi.assert_not_called()

    async def test_oversized_upload_rejected_before_download(self):
        with self.assertRaises(ValueError):
            await self.b.upload(7, {'document': {'file_size': 21*1024*1024, 'file_id': 'x'}})
        self.b.telegram.assert_not_called()

    async def test_queued_message_uses_official_steer(self):
        self.b.kimi.side_effect = [{'status': 'queued'}, {}]
        await self.b.handle(7, 'session-A', update())
        self.assertEqual(self.b.kimi.call_args_list[1].args, ('POST', '/sessions/session-A/prompts:steer'))
        self.assertEqual(self.b.kimi.call_args_list[1].kwargs['json'], {'prompt_ids': ['telegram-42-7']})

    async def test_running_prompt_does_not_steer_twice(self):
        await self.b.handle(7, 'session-A', update())
        self.assertEqual(self.b.kimi.call_count, 1)

    async def test_unknown_commands_are_not_agent_prompts(self):
        with self.assertRaises(ValueError):
            await self.b.handle(7, 'session-A', update(text='/dangerous'))
        self.b.kimi.assert_not_called()

    async def test_stale_approval_never_posts_answer(self):
        self.b.kimi.return_value = {'items': []}
        with self.assertRaises(ValueError):
            await self.b.answer_action(7, 'session-A', {'kind': 'approval', 'interaction': 'old', 'decision': 'approved'})
        self.assertEqual(self.b.kimi.call_count, 1)
        self.assertEqual(self.b.kimi.call_args.args[0], 'GET')

    async def test_old_session_buttons_cannot_change_current_session(self):
        self.b.store.put('button:abc', {'session': 'other', 'created': __import__('time').time(), 'action': {'kind': 'compact'}})
        q = {'update_id': 7, 'callback_query': {'id': 'cb', 'data': 'abc', 'from': {'id': 42}, 'message': update()['message']}}
        with self.assertRaises(ValueError):
            await self.b.handle(7, 'session-A', q)
        self.b.kimi.assert_not_called()

    async def test_unconfirmed_send_is_not_replayed(self):
        b = self.b
        del b.say
        b.send_lock = asyncio.Lock()
        b.last_send = 0
        b.redact = lambda text: text
        b.telegram.side_effect = OSError('ambiguous response')
        with self.assertRaises(OSError):
            await b.say('reply1', 'Hello')
        self.assertIsNone(await b.say('reply1', 'Hello'))
        self.assertEqual(b.telegram.call_count, 1)
        self.assertEqual(b.store.get('sent:reply1')['status'], 'uncertain')

    async def test_rate_limit_rejection_can_retry_safely(self):
        b = self.b
        del b.say
        b.send_lock = asyncio.Lock()
        b.last_send = 0
        b.redact = lambda text: text
        b.telegram.side_effect = [APIError(429, retry=True), {'message_id': 9}]
        with self.assertRaises(APIError):
            await b.say('reply1', 'Hello')
        self.assertEqual(await b.say('reply1', 'Hello'), 9)

    async def test_baseline_does_not_replay_historical_responses(self):
        self.b.kimi.return_value = {'turns': [{'turn_id': 't9', 'status': 'ended'}, {'turn_id': 't10', 'status': 'running'}]}
        await self.b.baseline('session-A')
        self.assertEqual(self.b.store.get('cursor:session-A'), 't9')
        self.b.say.assert_not_called()

    async def test_reply_to_question_uses_official_interaction(self):
        b = self.b
        b.pending_interaction = AsyncMock(return_value={})
        b.store.put('interaction:session-A:q1', {'request': {'questions': [{'id': 'color', 'allow_other': True}]}})
        await b.answer_text(7, 'session-A', {'session': 'session-A', 'interaction': 'q1', 'question': 'color'}, 'Blue')
        self.assertEqual(b.kimi.call_args.args, ('POST', '/sessions/session-A/interactions/q1'))
        self.assertEqual(b.kimi.call_args.kwargs['json']['answers']['color'], {'kind': 'other', 'text': 'Blue'})

if __name__ == '__main__':
    unittest.main()
