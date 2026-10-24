"""Conformance checks through the wire boundary; no agent framework required."""
import ast
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest

from examples.plain_agent import emit, run_turn, ScriptedModel


@pytest.fixture
def host(client):
    client.base_url = "http://testserver/api/"
    sid = client.post('/sessions', json={'name': 'conformance'}).json()['id']
    return client, sid


def test_discovery_and_version_rejection_do_not_mutate(host):
    http, sid = host
    discovery = http.get('/protocol').json()
    assert discovery['host_protocol_versions'] == ['0.1']
    assert discovery['prepare_schema']['properties']['capabilities']
    before = http.get(f'/sessions/{sid}').json()['revision']
    result = http.post(f'/sessions/{sid}/prepare', json={'supported_versions': ['unknown']}).json()
    assert result['ready'] is False and result['compiled'] is None
    assert result['issues'][0]['code'] == 'unsupported_version'
    assert http.get(f'/sessions/{sid}').json()['revision'] == before


def test_readiness_is_shared_and_preview_remains_available(host):
    http, sid = host
    emit(http, sid, 'tool.register', {'name':'example', 'description':'Example', 'input_schema':{'type':'object'}}, None)
    emit(http, sid, 'message.append', {'role': 'user', 'content': 'x'*1000}, 'turn')
    result = http.post(f'/sessions/{sid}/prepare', json={
        'budget_chars': 100, 'capabilities': {'roles': ['user'], 'tool_calls': False}}).json()
    assert not result['ready']
    assert {'over_budget', 'unsupported_role', 'unsupported_tool_calls'} <= {i['code'] for i in result['issues']}
    assert http.post(f'/sessions/{sid}/compile', json={'budget_chars': 100}).status_code == 200
    assert http.post(f'/sessions/{sid}/prepare', json={'capabilities': {'unknown': True}}).status_code == 422


def test_truncated_tools_cannot_be_used_for_inference(host):
    http, sid = host
    emit(http, sid, 'tool.register', {'name':'example', 'description':'Example schema tool', 'input_schema':{'type':'object'}}, None)
    emit(http, sid, 'group.upsert', {'id': 'tools', 'label': 'Tools', 'sources': ['tools'],
        'policy': {'strategy': 'truncate', 'max_chars': 64}}, None)
    result = http.post(f'/sessions/{sid}/prepare', json={}).json()
    assert not result['ready']
    assert 'truncated_tool_schema' in {i['code'] for i in result['issues']}


def exercise_python_host(http, sid):
    state_tool = next(tool for tool in http.get('/protocol').json()['context_tools'] if tool['name'] == 'context.state.set')
    emit(http, sid, 'tool.register', {'name': state_tool['name'], 'description': state_tool['description'],
        'input_schema': state_tool['input_schema'], 'tool_ref': 'context/'+state_tool['name']}, None)
    emit(http, sid, 'tool.register', {'name': 'multiply', 'description': 'Multiply', 'input_schema': {
        'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}}, None)
    assert '42' in run_turn(http, sid, 'Calculate and save', ScriptedModel(), {
        'multiply': lambda a, b: a*b, state_tool['name']: lambda **args: emit(http, sid, 'state.set', args, None)})
    state = http.get(f'/sessions/{sid}').json()['state']
    assert state['fields']['last_result']['value'] == 42
    assert len(state['calls']) == 2
    assert all(call['status'] == 'completed' for call in state['calls'].values())
    compiled = http.post(f'/sessions/{sid}/prepare', json={}).json()['compiled']
    assert len([m for m in compiled['model_input']['messages'] if m['role'] == 'tool']) == 2


def test_raw_http_loop_and_fresh_host(host):
    http, sid = host
    exercise_python_host(http, sid)
    def fresh_model(messages, tools):
        assert any('last_result' in m['content'] and '42' in m['content'] for m in messages)
        return {'content': '42'}
    assert run_turn(http, sid, 'Recall last_result', fresh_model, {}) == '42'


def test_transport_retries_and_revision_conflicts(host):
    http, sid = host
    body = {'protocol_version': '0.1', 'update_id': 'retry', 'source': 'conformance',
            'type': 'message.append', 'payload': {'role': 'user', 'content': 'once'}, 'expected_revision': 0}
    first = http.post(f'/sessions/{sid}/ingest', json=body)
    assert first.status_code == 201
    assert http.post(f'/sessions/{sid}/ingest', json=body).json()['duplicate']
    body['update_id'] = 'stale'
    assert http.post(f'/sessions/{sid}/ingest', json=body).status_code == 409


def test_javascript_host_continues_python_context(host):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for cross-language conformance')
    import uvicorn
    http, sid = host
    exercise_python_host(http, sid)
    # Bind an ephemeral port before starting; no project configuration or live database.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(http.app, log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.started
            script = Path(__file__).resolve().parents[1]/'examples'/'plain_host.mjs'
            result = subprocess.run([node, str(script), f'http://127.0.0.1:{sock.getsockname()[1]}/api', sid],
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            assert result.returncode == 0, result.stderr
            assert 'last_result' in result.stdout and '42' in result.stdout
            state = http.get(f'/sessions/{sid}').json()['state']
            assert state['messages'][-1]['content'] == 'Context received by JavaScript host'
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def test_core_and_plain_hosts_never_import_agent_frameworks():
    root = Path(__file__).resolve().parents[1]
    files = [p for p in (root/'context_protocol').rglob('*.py') if 'adapters' not in p.parts]
    files.append(root/'examples'/'plain_agent.py')
    for file in files:
        for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
            assert not any(m.startswith(('langchain', 'langgraph')) for m in modules), file
