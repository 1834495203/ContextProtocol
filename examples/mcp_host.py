"""MCP-first host with no LangChain dependency and no paid model calls.

python -m examples.mcp_host
python -m examples.mcp_host --session EXISTING_ID --host-id laptop
Discovers a local MCP server before the first model step, executes add(6,7),
records its result, and stores last_result through a registered local function.
"""
import argparse
import asyncio
from pathlib import Path
import sys
from uuid import uuid4
import httpx
from context_protocol.client import ContextClient
from context_protocol.tools import HostTools
from context_protocol.adapters.mcp import connect_stdio
from context_protocol.providers.context import ContextTools


async def demonstration(context, *, host_id='mcp-python'):
    turn_id = str(uuid4())
    with HostTools(context, host_id) as host:
        ContextTools(context, tools=('state.set',), turn_id=turn_id).register(host)
        async with connect_stdio(host, 'math', command=sys.executable, args=['-m', 'examples.mcp_server'],
                                 cwd=str(Path(__file__).resolve().parents[1])) as mcp:
            # Discovery has already happened; no model or business tool ran.
            print('Context:', context.session_id, 'Binding:', host.binding_id)
            prepared = host.prepare()
            if not prepared.ready:
                raise ValueError(prepared.issues)
            print('Discovered:', [tool.tool_ref for tool in prepared.compiled.model_input.tools])
            context.emit('message.append', {'role': 'user', 'content': '计算 6+7 并保存结果'}, turn_id)
            prepared = host.prepare()
            add = next(t for t in prepared.compiled.model_input.tools if t.tool_ref == 'math/add')
            call_id = str(uuid4())
            receipt = context.emit('message.append', {'role': 'assistant', 'content': '', 'tool_call_ids': [call_id]}, turn_id)
            context.emit('tool.call', {'call_id': call_id, 'name': add.name, 'arguments': {'a': 6, 'b': 7},
                                     'assistant_seq': receipt['seq']}, turn_id)
            result = await mcp.call(prepared, add.name, {'a': 6, 'b': 7})
            context.emit('tool.result', {'call_id': call_id, 'output': result.model_dump(mode='json'), 'is_error': result.isError}, turn_id)
            if result.isError:
                raise ValueError('MCP tool returned an error')
            import json
            value = json.loads(next(c.text for c in result.content if c.type == 'text'))
            prepared = host.prepare()
            state_tool = next(t for t in prepared.compiled.model_input.tools if t.tool_ref == 'context/context.state.set')
            arguments = {'key': 'last_result', 'value': value, 'value_type': 'number'}
            call_id = str(uuid4())
            receipt = context.emit('message.append', {'role': 'assistant', 'content': '', 'tool_call_ids': [call_id]}, turn_id)
            context.emit('tool.call', {'call_id': call_id, 'name': state_tool.name, 'arguments': arguments,
                                     'assistant_seq': receipt['seq']}, turn_id)
            saved = await host.call(prepared, state_tool.name, arguments)
            context.emit('tool.result', {'call_id': call_id, 'output': saved}, turn_id)
            context.emit('message.append', {'role': 'assistant', 'content': f'已保存 last_result={value}'}, turn_id)
            return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8010/api')
    parser.add_argument('--session')
    parser.add_argument('--host-id', default='mcp-python')
    args = parser.parse_args()
    with httpx.Client(base_url=args.url) as http:
        if args.session:
            context = ContextClient(http, args.session)
        else:
            response = http.post('/sessions', json={'name': 'Direct MCP host'})
            response.raise_for_status()
            context = ContextClient(http, response.json()['id'])
        print('Result:', asyncio.run(demonstration(context, host_id=args.host_id)))


if __name__ == '__main__':
    main()
