"""Legacy JSON façade. Live services use models/, domain/ and services/ directly.

Only this compatibility boundary and the SQL mapper convert dictionaries. Existing
stored archives and external pure-function callers retain their original wire API.
"""
from typing import Callable, Literal
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, model_validator
from .models.context import (Group, Policy, ContextState, JsonObject, ArchivedRequest, DEFAULT_GROUPS as GROUP_MODELS,
                             STATE_TOOL as TOOL_MODEL, new_state)
from .models.commands import ContextUpdate
from .models.compilation import SourceItem
from .domain.compiler import ContextCompiler
from .domain.sources.registry import default_sources
from .domain.updates.registry import default_registry
from .domain.updates.base import UpdateContext

VERSION = "0.1"
COMPILER_VERSION = "0.3"
DEFAULT_GROUPS = [group.model_dump(mode="json") for group in GROUP_MODELS]
STATE_TOOL = TOOL_MODEL.model_dump(mode="json")
BUILTIN_SOURCES = set(default_sources().providers)
SOURCE_PROVIDERS: dict[str, Callable] = {}

class EventInput(BaseModel):
    protocol_version: Literal["0.1"] = VERSION
    event_id: str = Field(min_length=1, max_length=128)
    type: str
    source: str = "manual"
    turn_id: str | None = None
    payload: JsonObject

    @model_validator(mode="after")
    def validate_legacy(self):
        if self.type == "model.request":
            ArchivedRequest.model_validate(self.payload.root | {"seq": 0})
            return self
        ContextUpdate.model_validate(self.to_update())
        return self

    def to_update(self):
        return {"protocol_version": self.protocol_version, "update_id": self.event_id,
                "type": self.type, "source": self.source, "turn_id": self.turn_id, "payload": self.payload.root}


def read_state(value) -> ContextState:
    """Normalize old missing component metadata at the JSON boundary only."""
    state = ContextState.model_validate(value)
    defaults = {group.id: group for group in GROUP_MODELS}
    defaults["tool_activity"] = Group(id="tool_activity", label="工具调用记录", kind="conversation", role="conversation", sources=["calls"])
    defaults["model_requests"] = Group(id="model_requests", label="历史模型请求存档", origin="external", sources=["blocks"], enabled=False)
    for group in state.groups:
        original = defaults.get(group.id)
        if original:
            for name in type(group).model_fields:
                if name not in group.model_fields_set:
                    setattr(group, name, getattr(original, name))
    return state


def initial_state():
    return new_state().model_dump(mode="json", by_alias=True)


def upgrade_state(state):
    normalized = read_state(state).model_dump(mode="json", by_alias=True)
    state.clear()
    state.update(normalized)
    return state


def apply_update(state, update):
    typed = read_state(state)
    payload = update["payload"]
    actor = payload.get("_context_actor", "legacy")
    if update["type"] == "model.request":
        record = ArchivedRequest.model_validate(payload | {"seq": update["seq"], "turn_id": update.get("turn_id")})
        if record.request_id in typed.requests:
            raise ValueError("模型请求 ID 已存在")
        typed.requests[record.request_id] = record
    else:
        command = ContextUpdate.model_validate({"update_id": update.get("event_id", update.get("update_id", "legacy")),
            "type": update["type"], "payload": payload, "source": update.get("source", "legacy"), "turn_id": update.get("turn_id")})
        default_registry().apply(UpdateContext(state=typed, seq=update["seq"], actor=actor, turn_id=command.root.turn_id), command.root)
    state.clear()
    state.update(typed.model_dump(mode="json", by_alias=True))
    return state

reduce_event = apply_update


def register_source(name, provider):
    if not isinstance(name, str) or not name.strip() or name in BUILTIN_SOURCES or not callable(provider):
        raise ValueError("来源名称必须非空、不能覆盖内置来源，且提供方必须可调用")
    SOURCE_PROVIDERS[name] = provider


class LegacySource:
    def __init__(self, name, callback):
        self.name, self.callback = name, callback
    def extract(self, state, group) -> list[SourceItem]:
        adapter = TypeAdapter(SourceItem)
        return [adapter.validate_python({"type": "data", **item}) for item in self.callback(state.model_dump(mode="json"), group.model_dump(mode="json"))]


def compile_context(state, budget_chars=24000):
    sources = default_sources()
    for name, callback in SOURCE_PROVIDERS.items():
        sources.register(LegacySource(name, callback))
    result = ContextCompiler(sources=sources).compile(read_state(state), budget_chars)
    return result.model_dump(mode="json", by_alias=True)
