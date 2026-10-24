"""Host-side tool registration, independent of agent frameworks.

Requires requirements-tools.txt for local JSON Schema argument validation.
"""
import inspect
from dataclasses import dataclass
from typing import Callable, Any
from uuid import uuid4
from .client import ContextClient
from .models.context import RegisteredTool
from .models.host import PrepareInput, PreparedContext


@dataclass(frozen=True)
class LocalTool:
    definition: RegisteredTool
    handler: Callable[..., Any]


def validate_tool_arguments(definition: RegisteredTool, arguments: dict):
    from jsonschema import Draft202012Validator
    Draft202012Validator(definition.input_schema.root).validate(arguments)


class HostTools:
    def __init__(self, context: ContextClient, host_id: str, *, lease_seconds: int = 3600):
        self.context = context
        self.binding_id = str(uuid4())
        self._local: dict[str, dict[str, tuple[RegisteredTool, object]]] = {}
        context.emit('host.open', {'binding_id': self.binding_id, 'host_id': host_id, 'lease_seconds': lease_seconds})

    def sync(self, provider_id: str, tools: list[RegisteredTool], *, kind='local', status='ready', expected_revision=None):
        self.context.emit('provider.sync', {'binding_id': self.binding_id, 'provider_id': provider_id,
            'kind': kind, 'status': status, 'tools': [tool.model_dump(mode='json') for tool in tools],
            'expected_binding_revision': expected_revision})

    def register(self, provider_id: str, function, *, name: str, description: str, input_schema: dict,
                 model_name: str | None = None):
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(input_schema)
        tool = RegisteredTool(name=name, description=description, input_schema=input_schema, model_name=model_name)
        current = dict(self._local.get(provider_id, {}))
        current[name] = (tool, function)
        self.sync(provider_id, [entry[0] for entry in current.values()])
        self._local[provider_id] = current
        return tool

    def register_provider(self, provider_id: str, tools: list[LocalTool]):
        """Atomically replace any local provider, including context capabilities."""
        from jsonschema import Draft202012Validator
        for tool in tools:
            Draft202012Validator.check_schema(tool.definition.input_schema.root)
            if not callable(tool.handler):
                raise TypeError('Local tool handler must be callable')
        self.sync(provider_id, [tool.definition for tool in tools])
        self._local[provider_id] = {tool.definition.name: (tool.definition, tool.handler) for tool in tools}

    def unregister_provider(self, provider_id: str):
        self.sync(provider_id, [])
        self._local.pop(provider_id, None)

    def unregister(self, provider_id: str, name: str):
        current = dict(self._local.get(provider_id, {}))
        current.pop(name, None)
        self.sync(provider_id, [entry[0] for entry in current.values()])
        self._local[provider_id] = current

    def prepare(self, budget_chars=24000, *, expected_revision=None) -> PreparedContext:
        return self.context.prepare(PrepareInput(binding_id=self.binding_id, budget_chars=budget_chars,
                                                expected_binding_revision=expected_revision))

    def check_call(self, prepared: PreparedContext, name: str):
        if not prepared.ready or prepared.binding_id != self.binding_id or prepared.compiled is None:
            raise ValueError('Use a ready preparation for this host binding')
        offered = next((t for t in prepared.compiled.model_input.tools if t.name == name), None)
        if offered is None:
            raise ValueError('Tool was not offered to the model')
        current = self.prepare(expected_revision=prepared.binding_revision)
        if not current.ready or not any(t.name == name and t.tool_ref == offered.tool_ref and
            t.input_schema == offered.input_schema for t in current.compiled.model_input.tools):
            raise ValueError('Tool binding or configuration changed; prepare again before execution')
        return offered

    async def call(self, prepared: PreparedContext, name: str, arguments: dict):
        offered = self.check_call(prepared, name)
        entry = next((entry for provider_id, provider in self._local.items() for key, entry in provider.items()
                      if provider_id + '/' + key == offered.tool_ref and (entry[0].model_name or entry[0].name) == name), None)
        if entry is None:
            raise ValueError('No local executor registered for this tool')
        validate_tool_arguments(entry[0], arguments)
        result = entry[1](**arguments)
        return await result if inspect.isawaitable(result) else result

    def renew(self):
        self.context.emit('host.renew', {'binding_id': self.binding_id})

    def close(self):
        self.context.emit('host.close', {'binding_id': self.binding_id})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
