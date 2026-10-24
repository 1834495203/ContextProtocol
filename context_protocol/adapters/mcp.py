"""Direct MCP discovery/execution using the official SDK; no LangChain dependency.

Connections and credentials live in the host, never in shared context state.
"""
from contextlib import asynccontextmanager
import asyncio
import hashlib
import re
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ToolListChangedNotification
from ..models.context import RegisteredTool
from ..tools import HostTools


def model_name(provider_id: str, tool_name: str) -> str:
    ref = provider_id + '/' + tool_name
    prefix = re.sub(r'[^a-zA-Z0-9_-]', '_', provider_id + '__' + tool_name)[:47]
    return prefix + '_' + hashlib.sha256(ref.encode()).hexdigest()[:12]


class MCPTools:
    def __init__(self, host: HostTools, provider_id: str, session: ClientSession):
        self.host, self.provider_id, self.session = host, provider_id, session
        self._names: dict[str, str] = {}
        self.dirty = True
        self._generation = 0
        self._refresh_lock = asyncio.Lock()

    async def invalidate(self):
        self._generation += 1
        self.dirty = True
        self._names = {}
        await asyncio.to_thread(self.host.sync, self.provider_id, [], kind='mcp', status='pending')

    async def refresh(self):
        """Fetch every tools/list page before atomically replacing this provider."""
        async with self._refresh_lock:
            return await self._refresh()

    async def _refresh(self):
        self.dirty = True
        await asyncio.to_thread(self.host.sync, self.provider_id, [], kind='mcp', status='pending')
        self._names = {}
        generation = self._generation
        try:
            tools = []
            cursor = None
            seen = set()
            while True:
                page = await self.session.list_tools(cursor=cursor)
                for tool in page.tools:
                    tools.append(RegisteredTool(name=tool.name, description=tool.description or '',
                        input_schema=tool.inputSchema, model_name=model_name(self.provider_id, tool.name)))
                cursor = page.nextCursor
                if cursor is None:
                    break
                if cursor in seen:
                    raise ValueError('Repeated MCP tools/list cursor')
                seen.add(cursor)
            if generation != self._generation:
                raise ValueError('MCP tool catalog changed during discovery; refresh again')
            await asyncio.to_thread(self.host.sync, self.provider_id, tools, kind='mcp')
            if generation != self._generation:
                raise ValueError('MCP tool catalog changed during discovery; refresh again')
            self._names = {tool.model_name: tool.name for tool in tools}
            self.dirty = False
            return tools
        except BaseException:
            self.dirty = True
            await asyncio.to_thread(self.host.sync, self.provider_id, [], kind='mcp', status='error')
            raise

    async def prepare(self, budget_chars=24000):
        if self.dirty:
            await self.refresh()
        return await asyncio.to_thread(self.host.prepare, budget_chars)

    async def call(self, prepared, name: str, arguments: dict):
        from jsonschema import Draft202012Validator
        offered = await asyncio.to_thread(self.host.check_call, prepared, name)
        if self.dirty:
            raise ValueError('MCP tool discovery is not current; prepare again')
        original_name = self._names.get(name)
        if original_name is None or offered.tool_ref != self.provider_id + '/' + original_name:
            raise ValueError('Tool does not belong to this MCP provider')
        Draft202012Validator(offered.input_schema.root).validate(arguments)
        try:
            return await self.session.call_tool(original_name, arguments)
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self):
        self.dirty = True
        self._names = {}
        await asyncio.to_thread(self.host.sync, self.provider_id, [], kind='mcp', status='error')


@asynccontextmanager
async def _connect(host, provider_id, transport):
    provider = None
    async def on_message(message):
        if provider is not None and isinstance(getattr(message, 'root', None), ToolListChangedNotification):
            await provider.invalidate()
    await asyncio.to_thread(host.sync, provider_id, [], kind='mcp', status='pending')
    try:
        async with transport as streams:
            async with ClientSession(streams[0], streams[1], message_handler=on_message) as session:
                await session.initialize()
                provider = MCPTools(host, provider_id, session)
                try:
                    await provider.refresh()
                    yield provider
                finally:
                    await provider.disconnect()
    except BaseException:
        await asyncio.to_thread(host.sync, provider_id, [], kind='mcp', status='error')
        raise


@asynccontextmanager
async def connect_stdio(host: HostTools, provider_id: str, *, command: str, args=None, cwd=None, env=None):
    """Launch a host-configured local MCP server and discover tools before yielding."""
    parameters = StdioServerParameters(command=command, args=args or [], cwd=cwd, env=env)
    async with _connect(host, provider_id, stdio_client(parameters)) as provider:
        yield provider


@asynccontextmanager
async def connect_http(host: HostTools, provider_id: str, *, url: str, headers=None):
    """Connect to a host-configured Streamable HTTP MCP server."""
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=300)) as http_client:
        async with _connect(host, provider_id, streamable_http_client(url, http_client=http_client)) as provider:
            yield provider
