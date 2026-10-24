"""Typed source-provider seam for built-in context data and future retrieval providers."""
from typing import Protocol
from ...models.context import ContextState, Group
from ...models.compilation import (SourceItem, DataSourceItem, FieldSourceItem, FieldValue,
    ToolSourceItem, ToolCatalogEntry, MessageSourceItem, CallSourceItem, MemorySourceItem,
    TraceSourceItem, RequestSourceItem)

class SourceProvider(Protocol):
    name: str
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]: ...

class BlocksSource:
    name = "blocks"
    def collection(self, state: ContextState):
        return state.blocks
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [DataSourceItem(id=item.id, type="text", content=item.content, seq=item.seq,
                origin=item.origin, content_schema=item.content_schema, source_uri=item.source_uri)
                for item in self.collection(state).values() if item.group_id == group.id]

class ItemsSource(BlocksSource):
    name = "items"
    def collection(self, state: ContextState):
        return state.items
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        items = super().extract(state, group)
        for item in items:
            item.type = "data"
        return items

class FieldsSource:
    name = "fields"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [FieldSourceItem(id=key, seq=value.seq, content=FieldValue(key=key, value=value.value,
                value_type=value.value_type, description=value.description)) for key, value in sorted(state.fields.items())]

class ToolsSource:
    name = "tools"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        # Preserve legacy builtin metadata in storage, but require explicit re-registration.
        return [ToolSourceItem(id=key, seq=value.seq, origin="external", content=ToolCatalogEntry(
                name=value.name, description=value.description, input_schema=value.input_schema, builtin=value.builtin,
                tool_ref=value.tool_ref))
                for key, value in sorted(state.tools.items()) if not value.builtin]

class MessagesSource:
    name = "messages"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [MessageSourceItem(id=f"message:{value.seq}", seq=value.seq, content=value,
                origin="internal" if value.role == "user" else "external", turn_id=value.turn_id, loop_id=value.loop_id)
                for value in state.messages]

class CallsSource:
    name = "calls"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [CallSourceItem(id=key, seq=value.seq, content=value, origin="external",
                turn_id=value.turn_id, loop_id=value.loop_id) for key, value in state.calls.items()]

class MemoriesSource:
    name = "memories"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [MemorySourceItem(id=f"memory:{value.seq}", seq=value.seq, content=value, turn_id=value.turn_id)
                for value in state.memories]

class TracesSource:
    name = "traces"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [TraceSourceItem(id=f"trace:{value.seq}", seq=value.seq, content=value, origin="external",
                turn_id=value.turn_id, loop_id=value.loop_id) for value in state.traces]

class RequestsSource:
    name = "requests"
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        return [RequestSourceItem(id=key, seq=value.seq, content=value, origin="external", turn_id=value.turn_id)
                for key, value in state.requests.items()]

class SourceRegistry:
    def __init__(self):
        self.providers: dict[str, SourceProvider] = {}
    def register(self, provider: SourceProvider) -> None:
        if not provider.name.strip() or provider.name in self.providers:
            raise ValueError("来源名称不能为空或重复")
        self.providers[provider.name] = provider
    def contains(self, name: str) -> bool:
        return name in self.providers
    def extract(self, state: ContextState, group: Group) -> list[SourceItem]:
        items: list[SourceItem] = []
        for name in group.sources:
            provider = self.providers.get(name)
            if provider:
                items.extend(provider.extract(state, group))
        return sorted(items, key=lambda item: (item.seq or 0, item.id))

def default_sources() -> SourceRegistry:
    registry = SourceRegistry()
    for source in (BlocksSource(), ItemsSource(), FieldsSource(), ToolsSource(), MessagesSource(),
                   CallsSource(), MemoriesSource(), TracesSource(), RequestsSource()):
        registry.register(source)
    return registry
