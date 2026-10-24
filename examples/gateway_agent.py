"""Pure HTTP agent. Register and select tools in the Web service first.

python -m examples.gateway_agent --session INSTANCE_ID
Only a deterministic model stand-in is used; there are no paid model calls.
"""
import argparse
from uuid import uuid4
import httpx


def run_turn(http, sid, prompt, model, max_steps=20):
    def post(path, body):
        response = http.post(path, json=body)
        response.raise_for_status()
        return response.json()
    path = '/sessions/'+sid
    turn_id = str(uuid4())
    def message(role, content, calls=()):
        return post(path+'/ingest', {'type': 'message.append', 'update_id': str(uuid4()), 'turn_id': turn_id,
            'payload': {'role': role, 'content': content, 'tool_call_ids': [call['id'] for call in calls]}})['update']['seq']
    message('user', prompt)
    for _ in range(max_steps):
        prepared = post(path+'/prepare', {})
        if not prepared['ready']:
            raise ValueError(prepared['issues'])
        if prepared['execution_mode'] != 'gateway':
            raise ValueError('Select central tool sources on this instance first')
        answer = model(prepared['compiled']['model_input'])
        calls = answer.get('tool_calls', [])
        seq = message('assistant', answer.get('content', ''), calls)
        if not calls:
            return answer.get('content', '')
        for call in calls:
            body = {'preparation_id': prepared['preparation_id'], 'call_id': call['id'], 'name': call['name'],
                    'arguments': call['arguments'], 'assistant_seq': seq, 'turn_id': turn_id}
            # On transport retries reuse this exact body. A pending execution is never blindly replayed.
            post(path+'/tools/call', body)
    raise RuntimeError('Agent step limit reached')


class ScriptedModel:
    def __init__(self):
        self.step = 0

    def __call__(self, model_input):
        self.step += 1
        if self.step == 1:
            tool = next(t for t in model_input['tools'] if t['tool_ref'].endswith(('/add', '/multiply')))
            return {'tool_calls': [{'id': str(uuid4()), 'name': tool['name'], 'arguments': {'a': 6, 'b': 7}}]}
        result = next(m for m in reversed(model_input['messages']) if m['role'] == 'tool')
        return {'content': 'Tool result: '+result['content']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8010/api')
    parser.add_argument('--session', required=True)
    parser.add_argument('--prompt', default='计算 6 和 7')
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=90) as http:
        print(run_turn(http, args.session, args.prompt, ScriptedModel()))


if __name__ == '__main__':
    main()
