"""The public guide stays readable offline and its examples match real APIs."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from context_protocol.controllers.system import router
from context_protocol.models.agent_guide import AgentGuide
from context_protocol.models.commands import ContextUpdate
from context_protocol.models.requests import ConnectInput
from context_protocol.models.host import PrepareInput


def test_guide_is_available_without_application_store_and_uses_request_origin():
    # No application state, repository, environment settings or DB lifecycle.
    application = FastAPI(root_path='/context')
    application.include_router(router)
    with TestClient(application, base_url='https://context.example:9443') as http:
        response = http.get('/api/agent-guide')
        assert response.status_code == 200
        guide = AgentGuide.model_validate(response.json())
        assert guide.service_base_url == 'https://context.example:9443/context'
        assert guide.api_base_url == 'https://context.example:9443/context/api'
        assert guide.links.openapi == 'https://context.example:9443/context/openapi.json'
        assert guide.links.protocol == 'https://context.example:9443/context/api/protocol'
        assert guide.links.markdown.endswith('/context/api/agent-guide?format=markdown')
        assert all('127.0.0.1:8010' not in link for link in guide.links.model_dump().values())


def test_guide_examples_match_published_schemas_and_openapi(client):
    guide = client.get('/api/agent-guide').json()
    spec = client.get('/openapi.json').json()
    assert '/api/agent-guide' in spec['paths']
    assert set(guide['schemas']) >= {'ContextUpdate', 'PrepareInput', 'PreparedContext', 'UpdateResult'}
    assert guide['schemas']['ContextUpdate'] == ContextUpdate.model_json_schema()
    for step in guide['workflow']:
        assert step['path'] in spec['paths']
        assert step['method'].lower() in spec['paths'][step['path']]
        if step['path'].endswith('/ingest'):
            ContextUpdate.model_validate(step['body'])
        elif step['id'] == 'connect':
            ConnectInput.model_validate(step['body'])
        elif step['id'] == 'prepare':
            PrepareInput.model_validate(step['body'])
    paths = {resource['path'] for resource in guide['resources']}
    assert {'docs/tool-integration.md', 'examples/mcp_host.py', 'examples/mcp_server.py'} <= paths
    assert all(resource['content'].strip() for resource in guide['resources'])
    assert all(not resource['path'].startswith(('.', '/')) for resource in guide['resources'])


def test_documented_http_workflow_can_be_followed_without_python_sdk(client):
    guide = client.get('/api/agent-guide').json()
    tid = client.get('/api/templates').json()[0]['id']
    sid = None
    assistant_seq = None
    prepared_revision = None
    for step in guide['workflow']:
        path = step['path'].replace('{tid}', tid)
        if sid:
            path = path.replace('{sid}', sid)
        body = deepcopy(step['body'])
        if step['id'] == 'tool_call':
            body['payload']['assistant_seq'] = assistant_seq
            # A real host executes its tool here; this conformance test checks the wire contract.
            prepared = client.post(f'/api/sessions/{sid}/prepare', json={
                'binding_id': 'example-connection-id', 'expected_binding_revision': prepared_revision}).json()
            assert prepared['ready']
        response = client.request(step['method'], path, **({'json': body} if body is not None else {}))
        assert response.is_success, (step['id'], response.text)
        result = response.json()
        if step['id'] == 'connect':
            sid = result['instance']['id']
        elif step['id'] == 'prepare':
            assert result['ready']
            assert result['compiled']['model_input']['tools'][0]['tool_ref'] == 'math/add'
            prepared_revision = result['binding_revision']
        elif step['id'] == 'assistant_call':
            assistant_seq = result['update']['seq']
    state = client.get('/api/sessions/'+sid).json()['state']
    assert state['calls']['example-call']['output'] == 13
    assert state['calls']['example-call']['status'] == 'completed'
    assert not state['host_bindings']['example-connection-id']['active']


def test_markdown_is_complete_and_reads_are_side_effect_free(client):
    before_sessions = client.get('/api/sessions').json()
    before_templates = client.get('/api/templates').json()
    response = client.get('/api/agent-guide?format=markdown')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/markdown')
    for text in ('# Context Protocol Agent 接入说明', 'host.open', 'provider.sync', 'binding_id',
                 'ready=false', 'update_id', 'call_id', 'examples/mcp_host.py', 'from context_protocol.tools import HostTools'):
        assert text in response.text
    assert client.get('/api/agent-guide?format=xml').status_code == 422
    assert client.get('/api/sessions').json() == before_sessions
    assert client.get('/api/templates').json() == before_templates
