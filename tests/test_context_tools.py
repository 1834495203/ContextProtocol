"""Context capabilities use the same registration and execution contract as other tools."""
import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from context_protocol.client import ContextClient
from context_protocol.models.context import STATE_TOOL, RegisteredTool, new_state
from context_protocol.providers.context import ContextTools
from context_protocol.tools import HostTools, LocalTool


@pytest.fixture
def context(client):
    client.base_url = 'http://testserver/api/'
    sid = client.post('/sessions', json={'name': 'explicit context provider'}).json()['id']
    return ContextClient(client, sid)


def test_new_context_and_discovery_do_not_advertise_an_executor(context):
    assert new_state().tools == {}
    assert context.view().state.tools == {}
    assert context.compile().model_input.tools == []
    before = context.view().revision
    catalog = context.client.get('/context-tools').json()
    assert catalog['registration_required']
    assert {tool['name'] for tool in catalog['tools']} == {'context.state.set', 'context.memory.append'}
    assert context.client.get('/protocol').json()['context_tools_auto_register'] is False
    assert context.view().revision == before
    assert context.prepare().compiled.model_input.tools == []


def test_context_provider_writes_selected_instance_and_turn_and_is_isolated(context):
    pytest.importorskip('jsonschema')
    host, other = HostTools(context, 'A'), HostTools(context, 'B')
    provider = ContextTools(context, tools=('state.set', 'memory.append'), turn_id=lambda: 'provider-turn')
    provider.register(host)
    host.register('business', lambda query: query, name='search', description='Search',
                  input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}}})
    prepared = host.prepare()
    assert prepared.ready
    assert {tool.tool_ref for tool in prepared.compiled.model_input.tools} == {
        'context/context.state.set', 'context/context.memory.append', 'business/search'}
    assert other.prepare().compiled.model_input.tools == []
    state_result = asyncio.run(host.call(prepared, 'context_state_set',
        {'key': 'phase', 'value': 'design', 'value_type': 'string'}))
    assert state_result == {'updated': 'phase', 'value': 'design'}
    memory_result = asyncio.run(host.call(prepared, 'context_memory_append', {'content': 'A reusable fact'}))
    state = context.view().state
    assert state.fields['phase'].value == 'design'
    assert state.memories[0].content == 'A reusable fact'
    assert state.memories[0].turn_id == 'provider-turn'
    assert state.memories[0].seq == memory_result['memory_seq']
    host.unregister_provider('context')
    assert {tool.name for tool in host.prepare().compiled.model_input.tools} == {'search'}
    with pytest.raises(ValueError, match='changed'):
        asyncio.run(host.call(prepared, 'context_state_set', {'key': 'bad', 'value': 1}))
    assert 'bad' not in context.view().state.fields


def test_context_provider_uses_normal_prompt_overlays_and_argument_validation(context):
    pytest.importorskip('jsonschema')
    host = HostTools(context, 'A')
    ContextTools(context).register(host)
    context.emit('tool.prompt.set', {'tool_ref': 'context/context.state.set', 'description': 'Save durable task state',
        'parameter_descriptions': {'/properties/key': 'Use stable project field names'}})
    prepared = host.prepare()
    spec = prepared.compiled.model_input.tools[0]
    assert spec.description == 'Save durable task state'
    assert spec.input_schema.root['properties']['key']['description'] == 'Use stable project field names'
    with pytest.raises(ValueError):
        asyncio.run(host.call(prepared, 'context_state_set', {'key': 'bad', 'value': 'text', 'value_type': 'number'}))
    assert 'bad' not in context.view().state.fields
    context.emit('tool.prompt.set', {'tool_ref': 'context/context.state.set', 'enabled': False})
    assert host.prepare().compiled.model_input.tools == []
    assert context.view().state.host_bindings[host.binding_id].providers['context'].tools
    with pytest.raises(ValueError):
        asyncio.run(host.call(prepared, 'context_state_set', {'key': 'disabled', 'value': 1}))


def test_legacy_auto_injected_metadata_is_preserved_but_not_executable(context, test_store):
    with test_store.repository.transaction(write=True) as tx:
        row = tx.context(context.session_id)
        row.state.tools[STATE_TOOL.name] = STATE_TOOL.model_copy(deep=True)
        tx.save_context(row)
    assert context.view().state.tools[STATE_TOOL.name].builtin
    assert context.prepare().compiled.model_input.tools == []
    # Re-registration and removal follow exactly the same rules as external tools.
    context.emit('tool.register', {'name': STATE_TOOL.name, 'description': 'Explicit executor',
                                  'input_schema': STATE_TOOL.input_schema.root})
    assert context.prepare().compiled.model_input.tools[0].description == 'Explicit executor'
    context.emit('tool.unregister', {'name': STATE_TOOL.name})
    assert context.prepare().compiled.model_input.tools == []


def test_selection_and_cross_instance_registration_are_explicit(context):
    pytest.importorskip('jsonschema')
    with pytest.raises(ValueError):
        ContextTools(context, tools=('unknown',))
    with pytest.raises(ValueError):
        ContextTools(context, tools=('state.set', 'context.state.set'))
    host = HostTools(context, 'A')
    ContextTools(context, tools=()).register(host)
    assert host.prepare().compiled.model_input.tools == []
    sid = context.client.post('/sessions', json={'name': 'other instance'}).json()['id']
    other = ContextClient(context.client, sid)
    with pytest.raises(ValueError, match='same context'):
        ContextTools(other).register(host)


def test_langchain_default_has_no_implicit_context_tools_and_adapts_async_providers(context):
    pytest.importorskip('langchain')
    from context_protocol.adapters.langchain import ContextAgent, local_tool_adapter
    from examples.langchain_agent import ScriptedModel
    agent = ContextAgent(context, ScriptedModel(), host_id='empty')
    assert agent.host_tools.prepare().compiled.model_input.tools == []
    async def echo(query):
        return query
    local = LocalTool(RegisteredTool(name='echo', input_schema={'type': 'object',
        'properties': {'query': {'type': 'string'}}, 'required': ['query']}), echo)
    assert asyncio.run(local_tool_adapter(local).ainvoke({'query': 'async provider'})) == 'async provider'
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        asyncio.run(local_tool_adapter(local).ainvoke({'query': 42}))
    from langchain_core.tools import StructuredTool
    def different_operation(key: str):
        """A separate operation using a conflicting name."""
        return key
    conflicting = StructuredTool.from_function(different_operation, name='context.state.set')
    with pytest.raises(ValueError, match='alias conflicts'):
        ContextAgent(context, ScriptedModel(), tools=[conflicting],
                     tool_providers={'context': ContextTools(context).tools})
    agent.close()


def test_context_tools_can_be_consumed_as_a_real_mcp_provider(context):
    pytest.importorskip('mcp')
    import uvicorn
    from context_protocol.adapters.mcp import connect_stdio
    # A separate MCP process talks to the context API, as any external provider would.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(context.client.app, log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.started
            host = HostTools(context, 'mcp-context-consumer')
            async def scenario():
                async with connect_stdio(host, 'context', command=sys.executable, cwd=str(Path(__file__).resolve().parents[1]),
                    args=['-m', 'context_protocol.adapters.context_mcp', '--api-url',
                          f'http://127.0.0.1:{sock.getsockname()[1]}/api', '--session', context.session_id,
                          '--tools', 'state.set', 'memory.append']) as provider:
                    prepared = await provider.prepare()
                    assert prepared.ready
                    spec = next(t for t in prepared.compiled.model_input.tools if t.tool_ref == 'context/context.state.set')
                    result = await provider.call(prepared, spec.name, {'key': 'via_mcp', 'value': 42, 'value_type': 'number'})
                    assert not result.isError
                    assert json.loads(result.content[0].text)['value'] == 42
                    assert context.view().state.fields['via_mcp'].value == 42
                assert not host.prepare().ready
            asyncio.run(scenario())
            host.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
