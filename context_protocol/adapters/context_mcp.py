"""Expose explicit context capabilities as an ordinary instance-bound MCP server.

python -m context_protocol.adapters.context_mcp --session UUID --tools state.set memory.append
"""
import argparse
import asyncio
import json
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from ..client import ContextClient
from ..providers.context import ContextTools


def create_server(provider: ContextTools) -> Server:
    server = Server('context-tools')
    tools = {tool.definition.name: tool for tool in provider.tools}

    @server.list_tools()
    async def list_tools():
        return [Tool(name=tool.definition.name, description=tool.definition.description,
                     inputSchema=tool.definition.input_schema.root) for tool in tools.values()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name not in tools:
            raise ValueError('Context capability is not enabled on this server')
        result = await asyncio.to_thread(tools[name].handler, **arguments)
        return [TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]

    return server


async def serve(provider: ContextTools):
    server = create_server(provider)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:8010/api')
    parser.add_argument('--session', required=True, help='Existing context instance ID')
    parser.add_argument('--tools', nargs='+', default=['state.set'], choices=['state.set', 'memory.append'])
    args = parser.parse_args()
    with httpx.Client(base_url=args.api_url, timeout=30) as http:
        context = ContextClient(http, args.session)
        context.view()  # Validate the configured instance before serving any capabilities.
        asyncio.run(serve(ContextTools(context, tools=args.tools)))


if __name__ == '__main__':
    main()
