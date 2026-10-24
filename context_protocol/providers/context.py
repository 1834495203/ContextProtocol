"""Explicit context capabilities, usable by any host like ordinary local tools."""
from collections.abc import Callable, Iterable
from uuid import uuid4
from ..client import ContextClient
from ..models.commands import StateSetCommand, MemoryAppendCommand
from ..models.context_tools import StateArguments, MemoryArguments, context_tool_catalog
from ..tools import HostTools, LocalTool


class ContextTools:
    def __init__(self, context: ContextClient, *, tools: Iterable[str] = ('state.set',),
                 turn_id: str | Callable[[], str | None] | None = None):
        self.context, self.turn_id = context, turn_id
        catalog = {tool.name: tool for tool in context_tool_catalog()}
        selected = [name if name.startswith('context.') else 'context.' + name for name in tools]
        if len(set(selected)) != len(selected) or any(name not in catalog for name in selected):
            raise ValueError('Select unique context tools from context_tool_catalog()')
        handlers = {'context.state.set': self.set_state, 'context.memory.append': self.append_memory}
        self.tools = [LocalTool(catalog[name], handlers[name]) for name in selected]

    def register(self, host: HostTools, *, provider_id='context'):
        if host.context.session_id != self.context.session_id or host.context.client is not self.context.client:
            raise ValueError('Context tools and host must use the same context client and instance')
        host.register_provider(provider_id, self.tools)

    def _turn(self):
        return self.turn_id() if callable(self.turn_id) else self.turn_id

    def set_state(self, **arguments):
        payload = StateArguments.model_validate(arguments)
        self.context.submit(StateSetCommand(update_id=str(uuid4()), turn_id=self._turn(), payload=payload))
        return {'updated': payload.key, 'value': payload.value}

    def append_memory(self, **arguments):
        payload = MemoryArguments.model_validate(arguments)
        receipt = self.context.submit(MemoryAppendCommand(update_id=str(uuid4()), turn_id=self._turn(), payload=payload))
        return {'memory_seq': receipt.seq}
