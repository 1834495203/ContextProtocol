"""Each update has a concrete payload; the wire format uses a discriminated union."""
from __future__ import annotations
import json
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, JsonValue, RootModel, StrictBool, model_validator
from .context import Model, Group, JsonObject, StateValue, StateDefinition, RegisteredTool, ToolPrompt

Identifier = Annotated[str, Field(min_length=1, max_length=200)]

class DeleteContent(Model):
    id: Identifier

class ReorderGroups(Model):
    ids: list[str]

class UpsertContent(Model):
    id: Identifier
    group_id: Identifier
    content: JsonValue
    origin: Literal["internal", "external"] | None = None
    content_schema: JsonObject | None = Field(default=None, alias="schema")
    source_uri: str | None = None

class UpsertBlock(UpsertContent):
    content: str

class SetField(StateValue):
    key: Identifier

class DeleteField(Model):
    key: Identifier

class DefineField(StateDefinition):
    key: Identifier

class RegisterTool(Model):
    name: Identifier
    description: str
    input_schema: JsonObject
    tool_ref: str | None = Field(default=None, max_length=265, pattern=r"^[a-z][a-z0-9_-]{0,63}/[^/]+$")

class UnregisterTool(Model):
    name: Identifier

class AppendMessage(Model):
    role: Literal["user", "assistant"]
    content: str
    loop_id: Identifier | None = None
    phase: str | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    build_id: str | None = None
    model_request_id: str | None = None

class EditMessage(Model):
    message_seq: Annotated[int, Field(strict=True, ge=1)]
    content: str

class CallTool(Model):
    call_id: Identifier
    name: Identifier
    arguments: JsonObject
    runtime_name: str | None = None
    assistant_seq: int | None = None
    loop_id: Identifier | None = None
    build_id: str | None = None

class ToolResult(Model):
    call_id: Identifier
    output: JsonValue
    is_error: StrictBool = False

class AppendMemory(Model):
    content: str
    kind: str = "long_term"
    source_uri: str | None = None

class AppendTrace(Model):
    kind: Identifier
    loop_id: Identifier
    content: str = ""

class Command(Model):
    payload: Model
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    protocol_version: Literal["0.1"] = "0.1"
    update_id: str = Field(min_length=1, max_length=128)
    source: str = Field(default="manual", min_length=1, max_length=200)
    turn_id: str | None = Field(default=None, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def check_size(self):
        if len(json.dumps(self.payload.model_dump(mode="json", by_alias=True), ensure_ascii=False, allow_nan=False).encode()) > 2_000_000:
            raise ValueError("单次更新不能超过 2 MB")
        return self


class OpenHost(Model):
    binding_id: Identifier
    host_id: Identifier
    lease_seconds: int = Field(default=3600, ge=30, le=86400)


class BindingReference(Model):
    binding_id: Identifier


class SyncProvider(BindingReference):
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: Literal["mcp", "local", "http", "langchain"]
    status: Literal["pending", "ready", "error"] = "ready"
    tools: list[RegisteredTool] = Field(default_factory=list, max_length=10000)
    expected_binding_revision: int | None = Field(default=None, ge=0)


class HostOpenCommand(Command):
    type: Literal["host.open"] = "host.open"
    payload: OpenHost


class HostCloseCommand(Command):
    type: Literal["host.close"] = "host.close"
    payload: BindingReference


class HostRenewCommand(Command):
    type: Literal["host.renew"] = "host.renew"
    payload: BindingReference


class ProviderSyncCommand(Command):
    type: Literal["provider.sync"] = "provider.sync"
    payload: SyncProvider


class SetToolPrompt(ToolPrompt):
    base_schema: JsonObject | None = None


class ToolPromptSetCommand(Command):
    type: Literal["tool.prompt.set"] = "tool.prompt.set"
    payload: SetToolPrompt


class DeleteToolPrompt(Model):
    id: str = Field(min_length=1, max_length=265)


class ToolPromptDeleteCommand(Command):
    type: Literal["tool.prompt.delete"] = "tool.prompt.delete"
    payload: DeleteToolPrompt

class GroupUpsertCommand(Command):
    type: Literal["group.upsert"] = "group.upsert"
    payload: Group


class GroupReorderCommand(Command):
    type: Literal["group.reorder"] = "group.reorder"
    payload: ReorderGroups


class GroupDeleteCommand(Command):
    type: Literal["group.delete"] = "group.delete"
    payload: DeleteContent


class BlockUpsertCommand(Command):
    type: Literal["block.upsert"] = "block.upsert"
    payload: UpsertBlock


class BlockDeleteCommand(Command):
    type: Literal["block.delete"] = "block.delete"
    payload: DeleteContent


class ItemUpsertCommand(Command):
    type: Literal["item.upsert"] = "item.upsert"
    payload: UpsertContent


class ItemDeleteCommand(Command):
    type: Literal["item.delete"] = "item.delete"
    payload: DeleteContent


class StateSetCommand(Command):
    type: Literal["state.set"] = "state.set"
    payload: SetField


class StateDeleteCommand(Command):
    type: Literal["state.delete"] = "state.delete"
    payload: DeleteField

class StateDefineCommand(Command):
    type: Literal["state.define"] = "state.define"
    payload: DefineField


class ToolRegisterCommand(Command):
    type: Literal["tool.register"] = "tool.register"
    payload: RegisterTool


class ToolUnregisterCommand(Command):
    type: Literal["tool.unregister"] = "tool.unregister"
    payload: UnregisterTool


class MessageAppendCommand(Command):
    type: Literal["message.append"] = "message.append"
    payload: AppendMessage


class MessageUpdateCommand(Command):
    type: Literal["message.update"] = "message.update"
    payload: EditMessage


class ToolCallCommand(Command):
    type: Literal["tool.call"] = "tool.call"
    payload: CallTool


class ToolResultCommand(Command):
    type: Literal["tool.result"] = "tool.result"
    payload: ToolResult


class MemoryAppendCommand(Command):
    type: Literal["memory.append"] = "memory.append"
    payload: AppendMemory


class TraceAppendCommand(Command):
    type: Literal["trace.append"] = "trace.append"
    payload: AppendTrace


UpdateCommand = Annotated[
    HostOpenCommand | HostCloseCommand | HostRenewCommand | ProviderSyncCommand
    | ToolPromptSetCommand | ToolPromptDeleteCommand
    | GroupUpsertCommand
    | GroupReorderCommand
    | GroupDeleteCommand
    | BlockUpsertCommand
    | BlockDeleteCommand
    | ItemUpsertCommand
    | ItemDeleteCommand
    | StateSetCommand
    | StateDeleteCommand
    | StateDefineCommand
    | ToolRegisterCommand
    | ToolUnregisterCommand
    | MessageAppendCommand
    | MessageUpdateCommand
    | ToolCallCommand
    | ToolResultCommand
    | MemoryAppendCommand
    | TraceAppendCommand,
    Field(discriminator="type"),
]


class ContextUpdate(RootModel[UpdateCommand]):
    pass
