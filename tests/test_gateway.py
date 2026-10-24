"""Agents only use the context HTTP API; discovery and execution stay server-side."""
import asyncio
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from context_protocol.app import create_app
from context_protocol.models.context import RegisteredTool
from context_protocol.tools import LocalTool


@pytest.fixture
def gateway_client(test_store):
    calls = []
    def multiply(a, b):
        calls.append((a, b))
        return {'answer': a*b}
    tool = LocalTool(RegisteredTool(name='multiply', description='Multiply integers', input_schema={
        'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}), multiply)
    async def uncertain(a, b):
        calls.append((a, b))
        raise asyncio.TimeoutError('Execution may already have happened')
    with TestClient(create_app(test_store, local_tool_providers={
        'business': [tool], 'uncertain': [LocalTool(tool.definition, uncertain)]})) as http:
        http.created_sources = []
        yield http, calls
    with test_store.repository.transaction(write=True) as tx:
        for source_id in http.created_sources:
            tx.delete_tool_source(source_id)


def source(http, connection):
    source_id = 'test-'+uuid4().hex
    response = http.post('/api/tool-sources', json={'id': source_id, 'title': 'Test tools', 'connection': connection})
    assert response.status_code == 200, response.text
    http.created_sources.append(source_id)
    return response.json()


def update(http, sid, kind, payload):
    response = http.post(f'/api/sessions/{sid}/updates', json={
        'type': kind, 'update_id': str(uuid4()), 'payload': payload})
    response.raise_for_status()
    return response.json()


def instance(http, sources):
    sid = http.post('/api/sessions', json={'name': 'Central tools agent'}).json()['id']
    update(http, sid, 'tool.sources.set', {'sources': sources})
    return sid


def prepare(http, sid):
    response = http.post(f'/api/sessions/{sid}/prepare', json={})
    assert response.status_code == 200, response.text
    return response.json()


def call_body(http, sid, prepared, name, arguments):
    call_id, turn_id = str(uuid4()), str(uuid4())
    response = http.post(f'/api/sessions/{sid}/ingest', json={'type': 'message.append',
        'update_id': str(uuid4()), 'turn_id': turn_id, 'payload': {
            'role': 'assistant', 'content': '', 'tool_call_ids': [call_id]}})
    response.raise_for_status()
    return {'preparation_id': prepared['preparation_id'], 'call_id': call_id,
            'name': name, 'arguments': arguments, 'assistant_seq': response.json()['update']['seq'], 'turn_id': turn_id}


def test_central_context_capability_needs_no_agent_registration(gateway_client):
    http, _ = gateway_client
    src = source(http, {'transport': 'context', 'tools': ['state.set', 'memory.append']})
    discovery = http.post('/api/tool-sources/'+src['id']+'/discover').json()
    assert len(discovery['tools']) == 2
    assert discovery['availability'] == 'unconfirmed'
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    assert prepared['ready'] and prepared['execution_mode'] == 'gateway'
    assert prepared['preparation_id'] and prepared['valid_until']
    spec = next(t for t in prepared['compiled']['model_input']['tools'] if t['tool_ref'].endswith('/context.state.set'))
    body = call_body(http, sid, prepared, spec['name'], {'key': 'phase', 'value': 'planning', 'value_type': 'string'})
    result = http.post(f'/api/sessions/{sid}/tools/call', json=body)
    assert result.status_code == 200, result.text
    assert not result.json()['is_error']
    state = http.get('/api/sessions/'+sid).json()['state']
    assert state['fields']['phase']['value'] == 'planning'
    assert state['calls'][body['call_id']]['status'] == 'completed'
    assert state['calls'][body['call_id']]['turn_id'] == body['turn_id']


def test_local_executor_runs_once_and_duplicate_payload_conflicts(gateway_client):
    http, calls = gateway_client
    src = source(http, {'transport': 'local', 'registry_key': 'business'})
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    assert prepared['ready']
    body = call_body(http, sid, prepared, 'multiply', {'a': 6, 'b': 7})
    result = http.post(f'/api/sessions/{sid}/tools/call', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['output'] == {'answer': 42}
    repeated = http.post(f'/api/sessions/{sid}/tools/call', json=body).json()
    assert repeated['duplicate']
    assert calls == [(6, 7)]
    changed = {**body, 'arguments': {'a': 7, 'b': 7}}
    assert http.post(f'/api/sessions/{sid}/tools/call', json=changed).status_code == 409
    http.post(f'/api/sessions/{sid}/tools/disconnect').raise_for_status()
    assert http.post(f'/api/sessions/{sid}/tools/call', json=body).json()['duplicate']
    assert calls == [(6, 7)]


def test_template_source_selection_is_inherited_and_synchronized(gateway_client):
    http, _ = gateway_client
    src = source(http, {'transport': 'context'})
    tid = http.post('/api/templates', json={'name': 'Central template'}).json()['template']['id']
    response = http.post(f'/api/templates/{tid}/updates', json={'type': 'tool.sources.set',
        'update_id': str(uuid4()), 'payload': {'sources': [src['id']]}})
    assert response.status_code == 200, response.text
    sid = http.post('/api/sessions', json={'name': 'Inherited', 'template_id': tid}).json()['id']
    prepared = prepare(http, sid)
    assert prepared['compiled']['model_input']['tools']
    http.post(f'/api/templates/{tid}/updates', json={'type': 'tool.sources.set',
        'update_id': str(uuid4()), 'payload': {'sources': []}}).raise_for_status()
    preview = http.get(f'/api/sessions/{sid}/preview', params={'binding_id':prepared['binding_id']}).json()
    assert preview['model_input']['tools'] == []
    assert prepare(http, sid)['compiled']['model_input']['tools'] == []


def test_source_change_revokes_prepared_calls_and_removal_does_not_erase_history(gateway_client):
    http, calls = gateway_client
    src = source(http, {'transport': 'local', 'registry_key': 'business'})
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    body = call_body(http, sid, prepared, 'multiply', {'a': 6, 'b': 7})
    changed = http.post('/api/tool-sources', json={'id': src['id'], 'title': 'Disabled',
        'connection': src['connection'], 'enabled': False, 'expected_revision': src['revision']})
    assert changed.status_code == 200
    assert http.post(f'/api/sessions/{sid}/tools/call', json=body).status_code == 409
    assert calls == []
    assert not prepare(http, sid)['ready']
    assert http.delete('/api/tool-sources/'+src['id']).status_code == 204
    unavailable = prepare(http, sid)
    assert not unavailable['ready'] and unavailable['compiled']['model_input']['tools'] == []


def test_ticket_instance_tool_selection_and_arguments_are_checked(gateway_client):
    http, calls = gateway_client
    src = source(http, {'transport': 'local', 'registry_key': 'business'})
    a, b = instance(http, [src['id']]), instance(http, [])
    prepared = prepare(http, a)
    body = call_body(http, b, prepared, 'multiply', {'a': 6, 'b': 7})
    assert http.post(f'/api/sessions/{b}/tools/call', json=body).status_code == 409
    invalid = call_body(http, a, prepared, 'multiply', {'a': 'bad', 'b': 7})
    assert http.post(f'/api/sessions/{a}/tools/call', json=invalid).status_code == 422
    absent = call_body(http, a, prepared, 'missing', {})
    assert http.post(f'/api/sessions/{a}/tools/call', json=absent).status_code in (409, 422)
    assert calls == []


def test_prompt_overrides_are_used_by_central_preparation(gateway_client):
    http, _ = gateway_client
    src = source(http, {'transport': 'local', 'registry_key': 'business'})
    sid = instance(http, [src['id']])
    update(http, sid, 'tool.prompt.set', {'tool_ref': src['id']+'/multiply', 'description': 'Use for exact products',
        'parameter_descriptions': {'/properties/a': 'First integer'}})
    spec = prepare(http, sid)['compiled']['model_input']['tools'][0]
    assert spec['description'] == 'Use for exact products'
    assert spec['input_schema']['properties']['a']['description'] == 'First integer'
    update(http, sid, 'tool.prompt.set', {'tool_ref': src['id']+'/multiply', 'enabled': False})
    assert prepare(http, sid)['compiled']['model_input']['tools'] == []


def test_real_mcp_source_is_discovered_and_called_by_the_service(gateway_client):
    pytest.importorskip('mcp')
    http, _ = gateway_client
    src = source(http, {'transport': 'stdio', 'command': sys.executable,
        'args': ['-m', 'examples.mcp_server'], 'cwd': str(Path(__file__).resolve().parents[1])})
    discovery = http.post('/api/tool-sources/'+src['id']+'/discover')
    assert discovery.status_code == 200, discovery.text
    assert {t['name'] for t in discovery.json()['tools']} == {'add', 'echo'}
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    assert prepared['ready'], prepared
    spec = next(t for t in prepared['compiled']['model_input']['tools'] if t['tool_ref'].endswith('/add'))
    body = call_body(http, sid, prepared, spec['name'], {'a': 6, 'b': 7})
    result = http.post(f'/api/sessions/{sid}/tools/call', json=body)
    assert result.status_code == 200, result.text
    assert not result.json()['is_error']
    assert result.json()['output']['content'][0]['text'] == '13'
    assert prepare(http, sid)['ready']


def test_failed_discovery_is_not_an_empty_ready_catalog(gateway_client):
    http, _ = gateway_client
    src = source(http, {'transport': 'local', 'registry_key': 'not-installed'})
    assert http.post('/api/tool-sources/'+src['id']+'/discover').status_code == 422
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    assert not prepared['ready']
    assert prepared['compiled']['model_input']['tools'] == []
    assert {i['code'] for i in prepared['issues']} >= {'provider_unavailable'}


def test_timeout_is_persistently_pending_and_never_automatically_replayed(gateway_client):
    http, calls = gateway_client
    src = source(http, {'transport':'local','registry_key':'uncertain'})
    sid = instance(http, [src['id']])
    body = call_body(http, sid, prepare(http, sid), 'multiply', {'a':6,'b':7})
    assert http.post(f'/api/sessions/{sid}/tools/call', json=body).status_code == 409
    assert http.post(f'/api/sessions/{sid}/tools/call', json=body).status_code == 409
    assert calls == [(6,7)]
    assert http.get('/api/sessions/'+sid).json()['state']['calls'][body['call_id']]['status'] == 'pending'


def test_expired_preparation_cannot_execute(gateway_client):
    http, calls = gateway_client
    src = source(http, {'transport':'local','registry_key':'business'})
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    body = call_body(http, sid, prepared, 'multiply', {'a':6,'b':7})
    gateway = http.app.state.gateway
    snapshot = gateway.preparations[prepared['preparation_id']]
    gateway.preparations[prepared['preparation_id']] = (snapshot[0], snapshot[1], 0)
    assert http.post(f'/api/sessions/{sid}/tools/call', json=body).status_code == 409
    assert calls == []


class ApiClient:
    def __init__(self, http):
        self.http = http
    def post(self, path, **kwargs):
        return self.http.post('/api'+path, **kwargs)
    def get(self, path, **kwargs):
        return self.http.get('/api'+path, **kwargs)


def test_pure_http_example_and_sdk_agent_need_no_tool_registry(gateway_client):
    from examples.gateway_agent import run_turn, ScriptedModel
    from context_protocol.client import ContextClient
    from context_protocol.gateway_agent import GatewayAgent
    http, calls = gateway_client
    src = source(http, {'transport':'local','registry_key':'business'})
    sid = instance(http, [src['id']])
    api = ApiClient(http)
    assert '42' in run_turn(api, sid, 'Multiply', ScriptedModel())
    scripted = ScriptedModel()
    def model(model_input):
        return scripted(model_input.model_dump(mode='json'))
    assert '42' in GatewayAgent(ContextClient(api, sid), model).run('Again')
    assert calls == [(6,7),(6,7)]


def test_langchain_model_uses_service_tools_without_executors(gateway_client):
    pytest.importorskip('langchain')
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from context_protocol.adapters.langchain import GatewayAgent
    from context_protocol.client import ContextClient
    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return 'gateway-test'
        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=tools)
        def _generate(self, messages, **kwargs):
            answer = AIMessage(content=messages[-1].content) if isinstance(messages[-1], ToolMessage) else AIMessage(
                content='', tool_calls=[{'id':str(uuid4()),'name':kwargs['tools'][0]['function']['name'],
                                         'args':{'a':6,'b':7},'type':'tool_call'}])
            return ChatResult(generations=[ChatGeneration(message=answer)])
    http, calls = gateway_client
    src = source(http, {'transport':'local','registry_key':'business'})
    sid = instance(http, [src['id']])
    answer = GatewayAgent(ContextClient(ApiClient(http), sid), Model()).run('Multiply')
    assert '42' in answer and calls == [(6,7)]


def test_javascript_agent_only_talks_to_context_service(gateway_client):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for cross-language conformance')
    import uvicorn
    http, calls = gateway_client
    src = source(http, {'transport':'local','registry_key':'business'})
    sid = instance(http, [src['id']])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(http.app, log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic()<deadline:
                time.sleep(.01)
            assert server.started
            script = Path(__file__).resolve().parents[1]/'examples'/'gateway_agent.mjs'
            result = subprocess.run([node, str(script), f'http://127.0.0.1:{sock.getsockname()[1]}/api', sid],
                                    capture_output=True, text=True, encoding='utf-8', timeout=20)
            assert result.returncode == 0, result.stderr
            assert '42' in result.stdout
            assert calls == [(6,7)]
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def test_source_configuration_survives_app_restart(gateway_client, test_store):
    http, _ = gateway_client
    src = source(http, {'transport':'context'})
    sid = instance(http, [src['id']])
    prepared = prepare(http, sid)
    body = call_body(http, sid, prepared, 'context_state_set', {'key':'saved','value':42})
    assert http.post(f'/api/sessions/{sid}/tools/call', json=body).status_code == 200
    with TestClient(create_app(test_store)) as restarted:
        assert restarted.get('/api/tool-sources/'+src['id']).status_code == 200
        old_binding = restarted.get('/api/sessions/'+sid).json()['state']['host_bindings'][prepared['binding_id']]
        assert not old_binding['active']
        assert restarted.post(f'/api/sessions/{sid}/tools/call', json=body).json()['duplicate']
        again = prepare(restarted, sid)
        assert again['ready'] and again['binding_id'] != prepared['binding_id']


def test_published_central_guide_workflow_is_executable(gateway_client):
    http, _ = gateway_client
    guide = http.get('/api/agent-guide').json()
    assert guide['preferred_execution_mode'] == 'gateway'
    tid = http.get('/api/templates').json()[0]['id']
    sid, ticket, assistant = None, None, None
    for step in guide['gateway_workflow']:
        body = step['body']
        path = step['path'].replace('{tid}', tid).replace('{source_id}', 'example-context')
        if sid:
            path = path.replace('{sid}', sid)
        if step['id'] == 'central_call':
            body['preparation_id'] = ticket
            body['assistant_seq'] = assistant
        result = http.request(step['method'], path, **({'json':body} if body is not None else {}))
        assert result.is_success, (step['id'], result.text)
        data = result.json()
        if step['id'] == 'central_source':
            http.created_sources.append(data['id'])
        elif step['id'] == 'connect':
            sid = data['instance']['id']
        elif step['id'] == 'central_prepare':
            assert data['ready']
            ticket = data['preparation_id']
        elif step['id'] == 'central_assistant':
            assistant = data['update']['seq']
    assert http.get('/api/sessions/'+sid).json()['state']['fields']['phase']['value'] == 'planning'


def test_central_mcp_streamable_http_source(gateway_client):
    pytest.importorskip('mcp')
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP('Central HTTP test', stateless_http=True)
    @mcp.tool()
    def add(a:int, b:int)->int:
        """Add integers."""
        return a+b
    http, _ = gateway_client
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic()<deadline:
                time.sleep(.01)
            assert server.started
            src = source(http, {'transport':'streamable_http','url':f'http://127.0.0.1:{sock.getsockname()[1]}/mcp'})
            sid = instance(http,[src['id']])
            prepared = prepare(http,sid)
            assert prepared['ready'], prepared
            spec = prepared['compiled']['model_input']['tools'][0]
            body = call_body(http,sid,prepared,spec['name'],{'a':6,'b':7})
            result = http.post(f'/api/sessions/{sid}/tools/call',json=body)
            assert result.status_code == 200, result.text
            assert result.json()['output']['content'][0]['text'] == '13'
            http.post(f'/api/sessions/{sid}/tools/disconnect').raise_for_status()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
