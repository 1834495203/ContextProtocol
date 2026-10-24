"""Per-host catalogs, reusable prompt overlays and framework-independent execution."""
import asyncio
import socket
import threading
import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from context_protocol.client import ContextClient
from context_protocol.models.context import RegisteredTool, JsonObject
from context_protocol.models.host import PrepareInput
from context_protocol.domain.tools import contract_digest
from context_protocol.tools import HostTools


SCHEMA = {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'Original query'}}, 'required': ['query']}


@pytest.fixture
def context(client):
    client.base_url = 'http://testserver/api/'
    sid = client.post('/sessions', json={'name': 'tool bindings'}).json()['id']
    return ContextClient(client, sid)


def tool(name='search', **kwargs):
    return RegisteredTool(name=name, description='Original search', input_schema=SCHEMA, **kwargs)


def issue_codes(result):
    return {issue.code for issue in result.issues}


def test_two_hosts_isolate_catalogs_and_replace_only_one_provider(context):
    a, b = HostTools(context, 'A'), HostTools(context, 'B')
    a.sync('docs', [tool()])
    a.sync('math', [tool('calculate')])
    b.sync('web', [tool('browse')])
    assert {t.name for t in a.prepare().compiled.model_input.tools} == {'search', 'calculate'}
    assert {t.name for t in b.prepare().compiled.model_input.tools} == {'browse'}
    assert 'binding_required' in issue_codes(context.prepare())
    a.sync('docs', [])
    assert {t.name for t in a.prepare().compiled.model_input.tools} == {'calculate'}
    assert {t.name for t in b.prepare().compiled.model_input.tools} == {'browse'}
    b.close()
    assert 'binding_unavailable' in issue_codes(b.prepare())
    assert a.prepare().ready


def test_discovery_unknown_error_and_empty_are_distinct(context):
    host = HostTools(context, 'A')
    host.sync('docs', [tool()])
    ready = host.prepare()
    host.sync('docs', [], status='pending')
    pending = host.prepare()
    assert not pending.ready and pending.compiled.model_input.tools == []
    assert 'provider_unavailable' in issue_codes(pending)
    host.sync('docs', [], status='error')
    assert not host.prepare().ready
    host.sync('docs', [])
    assert host.prepare().ready
    assert 'binding_changed' in issue_codes(host.prepare(expected_revision=ready.binding_revision))


def test_tool_prompts_change_both_native_schema_and_component_text(context):
    host = HostTools(context, 'A')
    host.sync('docs', [tool()])
    context.emit('tool.prompt.set', {'tool_ref': 'docs/search', 'description': 'Search project sources first',
        'base_schema': SCHEMA, 'parameter_descriptions': {'/properties/query': 'Use module names and error keywords'}})
    prepared = host.prepare()
    assert prepared.ready
    spec = prepared.compiled.model_input.tools[0]
    assert spec.description == 'Search project sources first'
    assert spec.input_schema.root['properties']['query']['description'] == 'Use module names and error keywords'
    assert 'Use module names and error keywords' in prepared.compiled.prompt
    raw = context.view().state.host_bindings[host.binding_id].providers['docs'].tools[0]
    assert raw.description == 'Original search'
    assert raw.input_schema.root == SCHEMA
    changed = {**SCHEMA, 'properties': {'query': {'type': 'integer'}}}
    host.sync('docs', [RegisteredTool(name='search', input_schema=changed)])
    assert 'incompatible_tool' in issue_codes(host.prepare())
    assert host.prepare().compiled.model_input.tools == []


def test_required_missing_optional_and_disabled_tools(context):
    host = HostTools(context, 'A')
    context.emit('tool.prompt.set', {'tool_ref': 'docs/search', 'description': 'Prepared before discovery'})
    assert host.prepare().ready
    context.emit('tool.prompt.set', {'tool_ref': 'docs/search', 'required': True})
    assert 'missing_required_tool' in issue_codes(host.prepare())
    host.sync('docs', [tool()])
    assert host.prepare().ready
    context.emit('group.upsert', {'id': 'tools', 'label': 'Tools', 'sources': ['tools'], 'enabled': False})
    assert 'missing_required_tool' in issue_codes(host.prepare())
    context.emit('group.upsert', {'id': 'tools', 'label': 'Tools', 'sources': ['tools'], 'enabled': True})
    context.emit('tool.prompt.set', {'tool_ref': 'docs/search', 'enabled': False})
    assert host.prepare().ready and host.prepare().compiled.model_input.tools == []


def test_local_methods_validate_inputs_and_reject_stale_bindings(context):
    pytest.importorskip('jsonschema')
    from jsonschema import ValidationError
    host, other = HostTools(context, 'A'), HostTools(context, 'B')
    executed = []
    host.register('local', lambda query: executed.append(query) or query, name='search', description='Search', input_schema=SCHEMA)
    prepared = host.prepare()
    other.sync('web', [tool('browse')])  # unrelated host updates do not invalidate A
    assert asyncio.run(host.call(prepared, 'search', {'query': 'test'})) == 'test'
    with pytest.raises(ValidationError):
        asyncio.run(host.call(prepared, 'search', {'query': 3}))
    host.unregister('local', 'search')
    with pytest.raises(ValueError, match='changed'):
        asyncio.run(host.call(prepared, 'search', {'query': 'stale'}))
    assert executed == ['test']


def test_duplicate_names_are_not_silently_routed_and_stale_sync_conflicts(context):
    host = HostTools(context, 'A')
    host.sync('one', [tool()])
    revision = host.prepare().binding_revision
    host.sync('two', [tool()])
    assert 'ambiguous_tool' in issue_codes(host.prepare())
    with pytest.raises(Exception) as exc:
        host.sync('one', [], expected_revision=revision)
    assert exc.value.response.status_code == 409
    host.sync('two', [tool(model_name='other_search')])
    assert host.prepare().ready


def test_expired_and_closed_bindings_survive_storage_but_cannot_execute(context, test_store):
    host = HostTools(context, 'A')
    host.sync('docs', [tool()])
    with test_store.repository.transaction(write=True) as tx:
        row = tx.context(context.session_id)
        row.state.host_bindings[host.binding_id].expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        tx.save_context(row)
    assert 'binding_unavailable' in issue_codes(host.prepare())
    assert host.binding_id in context.view().state.host_bindings
    with pytest.raises(Exception):
        host.renew()


def test_template_preserves_prompts_but_never_copies_runtime_bindings(context, client):
    host = HostTools(context, 'A')
    host.sync('docs', [tool()])
    context.emit('tool.prompt.set', {'tool_ref': 'docs/search', 'description': 'Shared prompt'})
    template = client.post('/templates', json={'name': 'Tool template', 'source_context_id': context.session_id}).json()
    assert template['state']['host_bindings'] == {}
    assert template['state']['tool_prompts']['docs/search']['description'] == 'Shared prompt'
    tid = template['template']['id']
    instance = client.post('/sessions', json={'name': 'New environment', 'template_id': tid}).json()
    client.post(f'/templates/{tid}/updates', json={'type': 'tool.prompt.set', 'update_id': 'template-prompt',
        'payload': {'tool_ref': 'docs/search', 'description': 'Updated shared prompt'}}).raise_for_status()
    state = client.get('/sessions/'+instance['id']).json()['state']
    assert state['tool_prompts']['docs/search']['description'] == 'Updated shared prompt'
    assert state['host_bindings'] == {}


def test_schema_fingerprints_ignore_annotations_but_preserve_values_and_property_names(context):
    a = {'type': 'object', 'properties': {'description': {'type': 'string', 'description': 'old'}}, 'const': {'description': 'x'}}
    b = {'type': 'object', 'properties': {'description': {'type': 'string', 'description': 'new'}}, 'const': {'description': 'x'}}
    assert contract_digest(JsonObject(a)) == contract_digest(JsonObject(b))
    b['const']['description'] = 'y'
    assert contract_digest(JsonObject(a)) != contract_digest(JsonObject(b))
    with pytest.raises(Exception) as exc:
        context.emit('tool.prompt.set', {'tool_ref': 'docs/search', 'base_schema': SCHEMA,
            'parameter_descriptions': {'/required': 'Must not overwrite required'}})
    assert exc.value.response.status_code == 422


def test_mcp_discovery_pagination_invalidation_and_failure_without_running_tools(context):
    pytest.importorskip('mcp')
    from context_protocol.adapters.mcp import MCPTools
    host = HostTools(context, 'A')
    class FakeSession:
        fail = False
        calls = []
        async def list_tools(self, cursor=None):
            if self.fail:
                raise RuntimeError('Discovery failed')
            name = 'search' if cursor is None else 'browse'
            return SimpleNamespace(tools=[SimpleNamespace(name=name, description=name, inputSchema=SCHEMA)],
                                   nextCursor='page2' if cursor is None else None)
        async def call_tool(self, name, args):
            self.calls.append(name)
            return 'done'
    session = FakeSession()
    provider = MCPTools(host, 'docs', session)
    async def scenario():
        await provider.refresh()
        assert session.calls == []
        prepared = await provider.prepare()
        assert {t.tool_ref for t in prepared.compiled.model_input.tools} == {'docs/search', 'docs/browse'}
        spec = next(t for t in prepared.compiled.model_input.tools if t.tool_ref == 'docs/search')
        assert await provider.call(prepared, spec.name, {'query': 'test'}) == 'done'
        await provider.invalidate()
        assert not host.prepare().ready
        with pytest.raises(ValueError):
            await provider.call(prepared, spec.name, {'query': 'stale'})
        assert (await provider.prepare()).ready
        session.fail = True
        with pytest.raises(RuntimeError):
            await provider.refresh()
        assert host.prepare().compiled.model_input.tools == []
    asyncio.run(scenario())


def test_real_mcp_stdio_and_local_function_without_langchain(context):
    pytest.importorskip('mcp')
    from examples.mcp_host import demonstration
    assert asyncio.run(demonstration(context)) == 13
    state = context.view().state
    assert state.fields['last_result'].value == 13
    assert len(state.calls) == 2 and all(c.status == 'completed' for c in state.calls.values())
    assert all(not b.active for b in state.host_bindings.values())
    assert 'math/add' in state.tool_catalog  # offline editing survives disconnect


def test_real_mcp_streamable_http_discovery_and_execution(context):
    pytest.importorskip('mcp')
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from context_protocol.adapters.mcp import connect_http
    mcp = FastMCP('HTTP conformance', stateless_http=True)
    @mcp.tool()
    def echo(query: str) -> str:
        """Return the query unchanged."""
        return query
    host = HostTools(context, 'http-environment')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.started
            async def scenario():
                async with connect_http(host, 'remote', url=f'http://127.0.0.1:{sock.getsockname()[1]}/mcp') as provider:
                    prepared = await provider.prepare()
                    assert prepared.ready
                    spec = prepared.compiled.model_input.tools[0]
                    assert spec.tool_ref == 'remote/echo'
                    result = await provider.call(prepared, spec.name, {'query': 'HTTP result'})
                    assert not result.isError
                    assert result.content[0].text == 'HTTP result'
                assert 'provider_unavailable' in issue_codes(host.prepare())
            asyncio.run(scenario())
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            host.close()


def test_langchain_supports_explicit_host_binding(context):
    pytest.importorskip('langchain')
    from examples.langchain_agent import ScriptedModel
    # Example wrapper is legacy; use the adapter directly with its business tool.
    from context_protocol.adapters.langchain import ContextAgent as BoundAgent
    from langchain_core.tools import StructuredTool
    from context_protocol.providers.context import ContextTools
    def multiply(a: int, b: int) -> int:
        """Multiply two numbers."""
        return a*b
    a = BoundAgent(context, ScriptedModel(), tools=[StructuredTool.from_function(multiply)], host_id='A',
                   tool_providers={'context': ContextTools(context).tools})
    b = BoundAgent(context, ScriptedModel(), tools=[], host_id='B')
    assert '42' in a.run('计算 6 乘 7 并保存')
    assert {t.name for t in a.host_tools.prepare().compiled.model_input.tools} == {'multiply', 'context_state_set'}
    assert b.host_tools.prepare().compiled.model_input.tools == []
    a.close()
    b.close()
