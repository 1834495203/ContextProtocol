from ...models.commands import (ToolRegisterCommand, ToolUnregisterCommand, MessageAppendCommand,
    MessageUpdateCommand, ToolCallCommand, ToolResultCommand, MemoryAppendCommand, TraceAppendCommand)
from ...models.context import ToolDefinition, Message, ToolCall, Memory, Trace, STATE_TOOL
from .base import UpdateContext, UpdateHandler


class RegisterToolHandler(UpdateHandler[ToolRegisterCommand]):
    command_type = ToolRegisterCommand

    def handle(self, context: UpdateContext, command: ToolRegisterCommand) -> None:
        context.require_producer()
        payload = command.payload
        context.state.tools[payload.name] = ToolDefinition(name=payload.name, description=payload.description,
                                                          input_schema=payload.input_schema, seq=context.seq)


class UnregisterToolHandler(UpdateHandler[ToolUnregisterCommand]):
    command_type = ToolUnregisterCommand

    def handle(self, context: UpdateContext, command: ToolUnregisterCommand) -> None:
        context.require_producer()
        if command.payload.name == STATE_TOOL.name:
            raise ValueError("内置状态工具不能移除")
        context.state.tools.pop(command.payload.name, None)


class AppendMessageHandler(UpdateHandler[MessageAppendCommand]):
    command_type = MessageAppendCommand

    def handle(self, context: UpdateContext, command: MessageAppendCommand) -> None:
        payload = command.payload
        if payload.role == "assistant":
            context.require_producer()
        context.state.messages.append(Message(role=payload.role, content=payload.content, seq=context.seq,
            turn_id=context.turn_id, loop_id=payload.loop_id, phase=payload.phase, tool_call_ids=payload.tool_call_ids,
            build_id=payload.build_id, model_request_id=payload.model_request_id))


class EditMessageHandler(UpdateHandler[MessageUpdateCommand]):
    command_type = MessageUpdateCommand

    def handle(self, context: UpdateContext, command: MessageUpdateCommand) -> None:
        message = next((m for m in context.state.messages if m.seq == command.payload.message_seq), None)
        if message is None or message.role != "user":
            raise ValueError("只能编辑已有的用户发言；Agent 观测数据只读")
        message.content = command.payload.content
        message.edited_seq = context.seq


class CallToolHandler(UpdateHandler[ToolCallCommand]):
    command_type = ToolCallCommand

    def handle(self, context: UpdateContext, command: ToolCallCommand) -> None:
        context.require_producer()
        payload = command.payload
        if payload.call_id in context.state.calls:
            raise ValueError("call_id 已存在")
        context.state.calls[payload.call_id] = ToolCall(call_id=payload.call_id, name=payload.name,
            arguments=payload.arguments, runtime_name=payload.runtime_name, assistant_seq=payload.assistant_seq,
            loop_id=payload.loop_id, seq=context.seq, turn_id=context.turn_id, build_id=payload.build_id)


class ToolResultHandler(UpdateHandler[ToolResultCommand]):
    command_type = ToolResultCommand

    def handle(self, context: UpdateContext, command: ToolResultCommand) -> None:
        context.require_producer()
        call = context.state.calls.get(command.payload.call_id)
        if call is None or call.status != "pending":
            raise ValueError("工具结果必须关联已有且未完成的调用")
        call.output = command.payload.output
        call.status = "error" if command.payload.is_error else "completed"
        call.result_seq = context.seq


class AppendMemoryHandler(UpdateHandler[MemoryAppendCommand]):
    command_type = MemoryAppendCommand

    def handle(self, context: UpdateContext, command: MemoryAppendCommand) -> None:
        payload = command.payload
        context.state.memories.append(Memory(content=payload.content, kind=payload.kind,
            source_uri=payload.source_uri, seq=context.seq, turn_id=context.turn_id))


class AppendTraceHandler(UpdateHandler[TraceAppendCommand]):
    command_type = TraceAppendCommand

    def handle(self, context: UpdateContext, command: TraceAppendCommand) -> None:
        context.require_producer()
        payload = command.payload
        context.state.traces.append(Trace(kind=payload.kind, loop_id=payload.loop_id,
            content=payload.content, seq=context.seq, turn_id=context.turn_id))
