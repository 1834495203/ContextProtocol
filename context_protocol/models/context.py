"""Provider-independent context entities. Dynamic maps contain typed records."""
from __future__ import annotations

from enum import StrEnum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class JsonObject(RootModel[dict[str, JsonValue]]):
    """Opaque caller-owned JSON, never a substitute for a business entity."""


class ValueType(StrEnum):
    JSON = "json"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class Policy(Model):
    strategy: str = Field(default="keep", min_length=1, max_length=64)
    limit: int = Field(default=12, ge=1, le=10000)
    max_chars: int = Field(default=6000, ge=64, le=1000000)
    unit: Literal["auto", "item", "turn", "loop"] = "auto"


class Group(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    kind: str = Field(default="text", min_length=1, max_length=64)
    role: Literal["system", "context", "conversation"] = "context"
    origin: Literal["internal", "external"] = "internal"
    sources: list[str] = Field(default_factory=lambda: ["blocks", "items"], max_length=32)
    policy: Policy = Field(default_factory=Policy)


class ContentItem(Model):
    id: str
    group_id: str
    content: JsonValue
    origin: Literal["internal", "external"] = "internal"
    seq: int = 0
    content_schema: JsonObject | None = Field(default=None, alias="schema")
    source_uri: str | None = None


class StateValue(Model):
    value: JsonValue
    value_type: ValueType = ValueType.JSON
    description: str = ""

    @model_validator(mode="after")
    def validate_value(self):
        valid = VALUE_VALIDATORS[self.value_type](self.value)
        if not valid:
            raise ValueError("value 与 value_type 不匹配")
        return self


def json_value(value: JsonValue) -> bool:
    return True


def number_value(value: JsonValue) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool)


VALUE_VALIDATORS = {
    ValueType.JSON: json_value,
    ValueType.STRING: lambda value: isinstance(value, str),
    ValueType.NUMBER: number_value,
    ValueType.BOOLEAN: lambda value: isinstance(value, bool),
    ValueType.OBJECT: lambda value: isinstance(value, dict),
    ValueType.ARRAY: lambda value: isinstance(value, list),
}


class StateField(StateValue):
    seq: int = 0


class StateDefinition(StateValue):
    """value is the default; runtime values remain in ContextState.fields."""


class ToolDefinition(Model):
    name: str
    description: str
    input_schema: JsonObject
    builtin: bool = False
    origin: Literal["external"] = "external"
    seq: int = 0
    tool_ref: str | None = None


class RegisteredTool(Model):
    """Provider contract. Callables, URLs and credentials remain in the host."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200, pattern=r"^[^/]+$")
    description: str = ""
    input_schema: JsonObject
    model_name: str | None = Field(default=None, min_length=1, max_length=200)


class ProviderCatalog(Model):
    kind: Literal["mcp", "local", "http", "langchain"]
    status: Literal["pending", "ready", "error"] = "pending"
    tools: list[RegisteredTool] = Field(default_factory=list)


class HostBinding(Model):
    id: str
    host_id: str
    managed_by: Literal['host', 'gateway'] = 'host'
    revision: int = 0
    active: bool = True
    expires_at: datetime
    lease_seconds: int = 3600
    providers: dict[str, ProviderCatalog] = Field(default_factory=dict)


class ToolPrompt(Model):
    model_config = ConfigDict(extra="forbid")
    tool_ref: str = Field(max_length=265, pattern=r"^[a-z][a-z0-9_-]{0,63}/[^/]+$")
    description: str | None = None
    parameter_descriptions: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    required: bool = False
    contract_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Message(Model):
    role: Literal["user", "assistant"]
    content: str
    seq: int
    turn_id: str | None = None
    loop_id: str | None = None
    phase: str | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    edited_seq: int | None = None
    build_id: str | None = None
    model_request_id: str | None = None


class ToolCall(Model):
    call_id: str
    preparation_id: str | None = None
    name: str
    arguments: JsonObject
    seq: int
    turn_id: str | None = None
    loop_id: str | None = None
    assistant_seq: int | None = None
    runtime_name: str | None = None
    status: Literal["pending", "completed", "error"] = "pending"
    output: JsonValue = None
    result_seq: int | None = None
    build_id: str | None = None


class Memory(Model):
    content: str
    seq: int
    turn_id: str | None = None
    kind: str = "long_term"
    source_uri: str | None = None


class Trace(Model):
    kind: str
    loop_id: str
    content: str = ""
    seq: int
    turn_id: str | None = None
    origin: Literal["external"] = "external"


class ArchivedRequest(Model):
    request_id: str
    build_id: str
    messages: list[JsonObject]
    tools: list[JsonObject]
    seq: int
    origin: Literal["external"] = "external"
    turn_id: str | None = None
    loop_id: str | None = None
    adapter_version: str | None = None
    model: str | None = None
    branch_id: str | None = None


class ContextState(Model):
    protocol_version: Literal["0.1"] = "0.1"
    groups: list[Group] = Field(default_factory=list)
    blocks: dict[str, ContentItem] = Field(default_factory=dict)
    items: dict[str, ContentItem] = Field(default_factory=dict)
    fields: dict[str, StateField] = Field(default_factory=dict)
    field_definitions: dict[str, StateDefinition] = Field(default_factory=dict)
    tools: dict[str, ToolDefinition] = Field(default_factory=dict)
    host_bindings: dict[str, HostBinding] = Field(default_factory=dict)
    tool_catalog: dict[str, RegisteredTool] = Field(default_factory=dict)
    tool_prompts: dict[str, ToolPrompt] = Field(default_factory=dict)
    tool_sources: list[str] | None = None
    messages: list[Message] = Field(default_factory=list)
    calls: dict[str, ToolCall] = Field(default_factory=dict)
    memories: list[Memory] = Field(default_factory=list)
    traces: list[Trace] = Field(default_factory=list)
    requests: dict[str, ArchivedRequest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def legacy_definitions(self):
        for key, field in self.fields.items():
            if key not in self.field_definitions:
                self.field_definitions[key] = StateDefinition(value=field.value, value_type=field.value_type,
                                                               description=field.description)
        return self


class StateToolArguments(StateValue):
    key: str


# Legacy description only. New contexts never advertise an executor implicitly.
STATE_TOOL = ToolDefinition(name="context.state.set", description="创建或更新当前上下文的自定义状态字段，由宿主适配器执行。",
                            input_schema=JsonObject(StateToolArguments.model_json_schema()), builtin=True)

DEFAULT_GROUPS = (
    Group(id="instructions", label="系统提示词", role="system"),
    Group(id="tools", label="工具描述与 Schema", kind="tools", origin="external", sources=["tools"]),
    Group(id="goals", label="设置的提示词", role="system"),
    Group(id="state", label="状态字段", kind="state", sources=["fields"]),
    Group(id="conversation", label="对话与 Agent Loop", kind="conversation", role="conversation",
          sources=["messages", "calls", "traces"], policy=Policy(strategy="recent", unit="turn")),
    Group(id="memory", label="记忆", kind="memory", sources=["memories", "blocks", "items"], policy=Policy(strategy="recent")),
)


def new_state() -> ContextState:
    return ContextState(groups=[group.model_copy(deep=True) for group in DEFAULT_GROUPS])
