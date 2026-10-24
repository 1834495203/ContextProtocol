from typing import Annotated, Literal
from pydantic import Field, JsonValue
from .context import (Model, Group, JsonObject, Message, ToolCall, Memory, Trace, ArchivedRequest)


class ToolSpec(Model):
    name: str
    description: str
    input_schema: JsonObject
    tool_ref: str | None = None


class ToolCatalogEntry(ToolSpec):
    builtin: bool = False


class FieldValue(Model):
    key: str
    value: JsonValue
    value_type: str
    description: str


class SourceItemBase(Model):
    id: str
    seq: int | None = None
    origin: Literal["internal", "external"] = "internal"
    content_schema: JsonObject | None = Field(default=None, alias="schema")
    source_uri: str | None = None
    turn_id: str | None = None
    loop_id: str | None = None


class DataSourceItem(SourceItemBase):
    type: Literal["text", "data"] = "data"
    content: JsonValue


class ToolSourceItem(SourceItemBase):
    type: Literal["tool"] = "tool"
    content: ToolCatalogEntry


class FieldSourceItem(SourceItemBase):
    type: Literal["field"] = "field"
    content: FieldValue


class MessageSourceItem(SourceItemBase):
    type: Literal["message"] = "message"
    content: Message


class CallSourceItem(SourceItemBase):
    type: Literal["call"] = "call"
    content: ToolCall


class MemorySourceItem(SourceItemBase):
    type: Literal["memory"] = "memory"
    content: Memory


class TraceSourceItem(SourceItemBase):
    type: Literal["trace"] = "trace"
    content: Trace


class RequestSourceItem(SourceItemBase):
    type: Literal["model_request"] = "model_request"
    content: ArchivedRequest


SourceItem = Annotated[DataSourceItem | ToolSourceItem | FieldSourceItem | MessageSourceItem |
                       CallSourceItem | MemorySourceItem | TraceSourceItem | RequestSourceItem,
                       Field(discriminator="type")]


class NativeToolCall(Model):
    id: str
    name: str
    arguments: JsonObject


class ModelMessage(Model):
    role: Literal["system", "user", "assistant", "tool", "observation"]
    content: str
    seq: int = 0
    group_id: str | None = None
    tool_calls: list[NativeToolCall] = Field(default_factory=list)
    call_id: str | None = None
    name: str | None = None
    status: str | None = None
    kind: str | None = None


class CompiledGroup(Group):
    text: str = ""
    items: list[SourceItem] = Field(default_factory=list)
    original_count: int = 0
    included_count: int = 0
    included_ids: list[str] = Field(default_factory=list)
    source_seqs: list[int] = Field(default_factory=list)
    chars: int = 0
    truncated: bool = False
    reason: str = ""
    messages: list[ModelMessage] = Field(default_factory=list)
    input_messages: list[ModelMessage] = Field(default_factory=list)


class ContextEnvelope(Model):
    version: str = "0.2"
    sections: list[CompiledGroup]


class ModelInput(Model):
    messages: list[ModelMessage] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)


class CompiledContext(Model):
    compiler_version: str = "0.3"
    groups: list[CompiledGroup]
    envelope: ContextEnvelope
    model_input: ModelInput
    prompt: str
    chars: int
    budget_chars: int
    over_budget: bool
    sha256: str
    warnings: list[str]
    session_id: str | None = None
    template_id: str | None = None
    revision: int | None = None


class QuotedGroup(Model):
    group_id: str
    text: str


class StructuredContent(Model):
    content: JsonValue
    content_schema: JsonObject = Field(alias="schema")
