"""Single-process central gateway. MCP sessions stay inside their owning task."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import hashlib
import os
import time
from uuid import uuid4
from ..models.commands import ContextUpdate
from ..models.context import RegisteredTool
from ..models.gateway import (StdioSource, HttpSource, ContextSource, LocalSource,
                              ToolSourceRow, GatewayCallInput, GatewayCallResult)
from ..models.host import PrepareInput, PreparedContext, HOST_PROTOCOL_VERSION
from ..services.errors import Conflict, NotFound
from ..services.tool_sources import source_view
from ..tools import HostTools


class ServiceContext:
    """Use the same SDK contract without HTTP calls back into this server."""
    def __init__(self, application, sid):
        self.application, self.session_id = application, sid
        self.client = self

    def submit(self, command):
        command.source = 'context-gateway'
        return self.application.update(self.session_id, ContextUpdate(command), actor='producer').update

    def emit(self, kind, payload, turn_id=None, update_id=None):
        command = ContextUpdate.model_validate({'type': kind, 'payload': payload, 'source': 'context-gateway',
            'turn_id': turn_id, 'update_id': update_id or str(uuid4())})
        return self.application.update(self.session_id, command, actor='producer').update.model_dump(mode='json')

    def view(self):
        return self.application.current(self.session_id)

    def prepare(self, request=None):
        return self.application.compilation.prepare(self.session_id, request or PrepareInput())


class CatalogHost:
    def __init__(self):
        self.tools = []

    def sync(self, provider_id, tools, **kwargs):
        self.tools = list(tools)


class MCPActor:
    def __init__(self, host, source: ToolSourceRow):
        self.host, self.source = host, source
        self.queue = asyncio.Queue()
        self.started = asyncio.get_running_loop().create_future()
        self.failure = None
        self.task = asyncio.create_task(self._run())

    async def start(self):
        await asyncio.wait_for(asyncio.shield(self.started), 30)

    async def _run(self):
        try:
            from ..adapters.mcp import connect_stdio, connect_http
            connection = self.source.connection
            if isinstance(connection, StdioSource):
                env = {key: os.environ[ref] for key, ref in connection.env_refs.items()} or None
                transport = connect_stdio(self.host, self.source.id, command=connection.command,
                    args=connection.args, cwd=connection.cwd, env=env)
            else:
                headers = {key: os.environ[ref] for key, ref in connection.header_env_refs.items()}
                transport = connect_http(self.host, self.source.id, url=connection.url, headers=headers)
            async with transport as provider:
                if not self.started.done():
                    self.started.set_result(None)
                while True:
                    item = await self.queue.get()
                    if item is None:
                        break
                    operation, arguments, future = item
                    try:
                        if operation == 'refresh':
                            result = await provider.refresh()
                        else:
                            result = await provider.call(*arguments)
                        if not future.done():
                            future.set_result(result)
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)
                        raise
        except BaseException as exc:
            self.failure = type(exc).__name__
            if not self.started.done():
                self.started.set_exception(RuntimeError(self.failure))
        finally:
            while not self.queue.empty():
                item = self.queue.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(RuntimeError('MCP connection closed'))

    async def request(self, operation, *arguments):
        if self.task.done():
            raise RuntimeError('MCP connection is unavailable')
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        await self.queue.put((operation, arguments, future))
        return await asyncio.wait_for(asyncio.shield(future), 60)

    async def close(self):
        if not self.task.done():
            await self.queue.put(None)
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 10)
        except asyncio.TimeoutError:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


@dataclass
class InstanceRuntime:
    host: HostTools
    sources: tuple[str, ...]
    actors: dict[str, MCPActor] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)
    turn_id: str | None = None


class ToolGateway:
    def __init__(self, application, *, local_tool_providers=None):
        self.application = application
        self.locals = local_tool_providers or {}
        self.runtimes = {}
        self.locks = {}
        self.preparations = {}

    async def start(self):
        # Persisted metadata must not claim that connections survived a service restart.
        for instance in self.application.sessions():
            current = self.application.current(instance.id)
            for binding in current.state.host_bindings.values():
                if binding.managed_by == 'gateway' and binding.active:
                    ServiceContext(self.application, instance.id).emit('host.close', {'binding_id':binding.id})

    def _lock(self, sid):
        return self.locks.setdefault(sid, asyncio.Lock())

    def _local_tools(self, source, sid=None, runtime=None):
        if isinstance(source.connection, ContextSource):
            if sid is None:
                from ..models.context_tools import context_tool_catalog
                selected = {'context.'+name for name in source.connection.tools}
                return [tool for tool in context_tool_catalog() if tool.name in selected]
            from ..providers.context import ContextTools
            return ContextTools(ServiceContext(self.application, sid), tools=source.connection.tools,
                                turn_id=lambda: runtime.turn_id).tools
        if source.connection.registry_key not in self.locals:
            raise ValueError('Local tool registry key is not installed on this server')
        return list(self.locals[source.connection.registry_key])

    async def discover(self, source_id):
        source = self.application.tool_sources.get(source_id)
        if not source.enabled:
            raise ValueError('Tool source is disabled')
        actor = None
        try:
            if isinstance(source.connection, (ContextSource, LocalSource)):
                tools = self._local_tools(source)
                definitions = tools if isinstance(source.connection, ContextSource) else [tool.definition for tool in tools]
            else:
                host = CatalogHost()
                actor = MCPActor(host, source)
                await actor.start()
                definitions = list(host.tools)
            return self.application.tool_sources.discovered(source_id, source.revision, definitions)
        except Exception as exc:
            self.application.tool_sources.discovered(source_id, source.revision, [], type(exc).__name__)
            raise ValueError('Tool discovery failed: '+type(exc).__name__) from None
        finally:
            if actor:
                await actor.close()

    async def _close_runtime(self, sid):
        runtime = self.runtimes.pop(sid, None)
        if runtime:
            for actor in runtime.actors.values():
                await actor.close()
            try:
                runtime.host.close()
            except Exception:
                pass

    async def invalidate(self, source_id):
        for sid in list(self.runtimes):
            async with self._lock(sid):
                runtime = self.runtimes.get(sid)
                if runtime and source_id in runtime.sources:
                    await self._close_runtime(sid)

    async def _ensure(self, sid):
        current = self.application.current(sid)
        if current.state.tool_sources is None:
            raise Conflict('Select central tool sources on this instance or its template first')
        selected = tuple(current.state.tool_sources)
        runtime = self.runtimes.get(sid)
        if runtime:
            binding = current.state.host_bindings.get(runtime.host.binding_id)
            if selected != runtime.sources or binding is None or not binding.active or binding.expires_at <= datetime.now(timezone.utc):
                await self._close_runtime(sid)
                runtime = None
        if runtime is None:
            runtime = InstanceRuntime(HostTools(ServiceContext(self.application, sid), 'context-gateway', managed_by='gateway'), selected)
            self.runtimes[sid] = runtime
        runtime.host.renew()
        for source_id in selected:
            source = None
            try:
                source = self.application.tool_sources.get(source_id)
                if not source.enabled:
                    raise ValueError('Tool source is disabled')
                actor = runtime.actors.get(source_id)
                changed = runtime.revisions.get(source_id) != source.revision
                if changed and actor:
                    await actor.close()
                    runtime.actors.pop(source_id, None)
                    actor = None
                if isinstance(source.connection, (ContextSource, LocalSource)):
                    if changed:
                        tools = self._local_tools(source, sid, runtime)
                        runtime.host.register_provider(source_id, tools)
                        self.application.tool_sources.discovered(source_id, source.revision, [tool.definition for tool in tools])
                else:
                    if actor is None or actor.task.done():
                        if actor:
                            await actor.close()
                        actor = MCPActor(runtime.host, source)
                        runtime.actors[source_id] = actor
                        await actor.start()
                    else:
                        # Discover before each model input, without executing business tools.
                        await actor.request('refresh')
                    definitions = self.application.current(sid).state.host_bindings[runtime.host.binding_id].providers[source_id].tools
                    self.application.tool_sources.discovered(source_id, source.revision, definitions)
                runtime.revisions[source_id] = source.revision
            except Exception as exc:
                runtime.host.sync(source_id, [], kind='mcp' if source and isinstance(source.connection, (StdioSource, HttpSource)) else 'local', status='error')
                runtime.revisions.pop(source_id, None)
        return runtime

    async def prepare(self, sid, request: PrepareInput):
        if HOST_PROTOCOL_VERSION not in request.supported_versions:
            return self.application.compilation.prepare(sid, request)
        async with self._lock(sid):
            runtime = await self._ensure(sid)
            bound = request.model_copy(update={'binding_id': runtime.host.binding_id})
            result = self.application.compilation.prepare(sid, bound)
            result.execution_mode = 'gateway'
            if result.ready:
                now = time.monotonic()
                self.preparations = {key: value for key, value in self.preparations.items() if value[2] > now}
                if len(self.preparations) >= 1024:
                    self.preparations.pop(next(iter(self.preparations)))
                result.preparation_id = str(uuid4())
                result.valid_until = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
                self.preparations[result.preparation_id] = (sid, result.model_copy(deep=True), now+300)
            return result

    async def call(self, sid, request: GatewayCallInput):
        async with self._lock(sid):
            current = self.application.current(sid)
            previous = current.state.calls.get(request.call_id)
            if previous:
                if (previous.preparation_id != request.preparation_id or previous.name != request.name or
                    previous.arguments != request.arguments or previous.assistant_seq != request.assistant_seq or previous.turn_id != request.turn_id):
                    raise Conflict('call_id was used for a different tool request')
                if previous.status == 'pending':
                    raise Conflict('Tool execution is pending or its outcome is unknown; do not replay it automatically')
                return GatewayCallResult(call_id=request.call_id, output=previous.output,
                    is_error=previous.status == 'error', duplicate=True, revision=current.revision)
            snapshot = self.preparations.get(request.preparation_id)
            if snapshot is None or snapshot[0] != sid or snapshot[2] <= time.monotonic():
                raise Conflict('Preparation is missing or expired; prepare model input again')
            runtime = self.runtimes.get(sid)
            if runtime is None or tuple(current.state.tool_sources or ()) != runtime.sources:
                raise Conflict('Tool sources changed; prepare model input again')
            prepared = snapshot[1]
            spec = runtime.host.check_call(prepared, request.name)
            source_id = spec.tool_ref.split('/', 1)[0]
            source = self.application.tool_sources.get(source_id)
            if not source.enabled or source.revision != runtime.revisions.get(source_id):
                raise Conflict('Tool source changed; prepare model input again')
            message = next((m for m in current.state.messages if m.seq == request.assistant_seq), None)
            if message is None or message.role != 'assistant' or request.call_id not in message.tool_call_ids or message.turn_id != request.turn_id:
                raise ValueError('Tool call must reference its recorded assistant message and turn')
            from jsonschema import Draft202012Validator
            try:
                Draft202012Validator(spec.input_schema.root).validate(request.arguments.root)
            except Exception:
                raise ValueError('Tool arguments do not match the offered schema') from None
            runtime.turn_id = request.turn_id
            digest = hashlib.sha256(request.call_id.encode()).hexdigest()
            client = runtime.host.context
            client.emit('tool.call', {'call_id': request.call_id, 'name': request.name, 'arguments': request.arguments.root,
                'assistant_seq': request.assistant_seq, 'preparation_id': request.preparation_id}, request.turn_id, 'gateway-call-'+digest)
            try:
                if source_id in runtime.actors:
                    response = await runtime.actors[source_id].request('call', prepared, request.name, request.arguments.root)
                    output, failed = response.model_dump(mode='json'), response.isError
                else:
                    output = await asyncio.wait_for(runtime.host.call(prepared, request.name, request.arguments.root), 60)
                    failed = False
            except asyncio.TimeoutError:
                raise Conflict('Execution timed out and may have completed; inspect the pending call before recovery') from None
            except Exception as exc:
                output, failed = {'error': type(exc).__name__}, True
            client.emit('tool.result', {'call_id': request.call_id, 'output': output, 'is_error': failed},
                        request.turn_id, 'gateway-result-'+digest)
            return GatewayCallResult(call_id=request.call_id, output=output, is_error=failed,
                                     revision=self.application.current(sid).revision)

    async def close(self):
        for sid in list(self.runtimes):
            async with self._lock(sid):
                await self._close_runtime(sid)
        self.preparations.clear()

    async def disconnect(self, sid):
        async with self._lock(sid):
            await self._close_runtime(sid)
