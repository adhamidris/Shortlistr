"""KAP 1.0 turn records normalized for the bridge's presentation layer."""

def turn_view(turn):
    frames = []
    prompt_ids = []
    for item in turn.get('items', []):
        if item['type'] == 'user_message':
            prompt_ids.extend(item.get('prompt_ids', []))
        if item['type'] == 'tool_call':
            state = 'running' if item.get('status') == 'running' and item.get('stage') == 'executing' else item.get('outcome', 'pending')
            frames.append({'kind': 'tool', 'toolCallId': item['item_id'], 'name': item['name'],
                           'input': item.get('input'), 'state': state})
    return {'kind': 'turn', 'turnId': turn['turn_id'], 'ordinal': turn['ordinal'],
            'triggerPromptId': prompt_ids[0] if prompt_ids else None,
            'prompt_ids': prompt_ids, 'endedAt': (turn.get('ended_at') or True) if turn['status'] == 'ended' else None,
            'state': turn.get('outcome', 'running'), 'error': (turn.get('error') or {}).get('message'),
            'steps': [{'frames': frames}], 'raw': turn}


class LiveActivity:
    """Latest turn for presentation, rebuilt by every subscription snapshot.

    Text deltas and tool output are deliberately not retained by the UI.
    """
    def __init__(self):
        self.turn = None
        self.ready = False

    def apply(self, event):
        kind = event['type']
        if kind == 'turns':
            turns = event.get('turns', [])
            self.turn = self.clean(turns[-1]) if turns else None
            self.ready = True
        elif kind in {'turn.started', 'turn.ended'}:
            turn = event['turn']
            if self.turn and turn['ordinal'] < self.turn['ordinal']:
                return
            items = self.turn['items'] if self.turn and self.turn['turn_id'] == turn['turn_id'] else []
            self.turn = self.clean(dict(turn, items=items))
            self.ready = True
        elif kind == 'turns.removed':
            if self.turn and self.turn['turn_id'] in event['turn_ids']:
                self.turn = None
        elif self.turn and event.get('turn_id') == self.turn['turn_id']:
            items = self.turn['items']
            if kind in {'item.started', 'item.ended'}:
                item = self.clean_item(event['item'])
                for n, old in enumerate(items):
                    if old['item_id'] == item['item_id']:
                        items[n] = item
                        break
                else:
                    items.append(item)
            elif kind == 'item.updated':
                for item in items:
                    if item['item_id'] == event['item_id']:
                        item.update(self.clean_item(event['set']))
                        break
            elif kind == 'item.removed':
                self.turn['items'] = [i for i in items if i['item_id'] != event['item_id']]

    @staticmethod
    def clean_item(item):
        return {k: v for k, v in item.items() if k in {
            'type', 'item_id', 'prompt_ids', 'name', 'status', 'stage', 'input', 'outcome'}}

    @classmethod
    def clean(cls, turn):
        return {**{k: v for k, v in turn.items() if k != 'items'},
                'items': [cls.clean_item(i) for i in turn.get('items', [])]}
