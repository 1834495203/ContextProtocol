"""Canonical message linkage shared by every framework adapter."""
from ..models.compilation import (SourceItem, MessageSourceItem, CallSourceItem, TraceSourceItem,
                                  ModelMessage, NativeToolCall)
from ..models.context import ToolCall
from .rendering import as_text

class MessageAssembler:
    def assemble(self, items: list[SourceItem]) -> list[ModelMessage]:
        messages = {item.content.seq: item.content for item in items if isinstance(item, MessageSourceItem)}
        calls = [item.content for item in items if isinstance(item, CallSourceItem)]
        output: list[ModelMessage] = []
        batches: dict[int, list[ToolCall]] = {}
        for call in calls:
            if call.status == "pending":
                output.append(ModelMessage(seq=call.seq, role="observation", content=as_text(call), kind="pending_call"))
                continue
            owner = next((m.seq for m in messages.values() if call.call_id in m.tool_call_ids), call.assistant_seq or call.seq)
            batches.setdefault(owner, []).append(call)
        for seq, message in sorted(messages.items()):
            if message.role == "user":
                output.append(ModelMessage(seq=seq, role="user", content=message.content))
                continue
            batch = batches.pop(seq, [])
            if message.content or batch:
                output.extend(self.assistant(seq, message.content, batch))
        for seq, batch in batches.items():
            output.extend(self.assistant(seq, "", batch))
        for item in items:
            if isinstance(item, TraceSourceItem):
                if item.content.kind == "reasoning":
                    output.append(ModelMessage(seq=item.seq or 0, role="observation", kind="reasoning", content=item.content.content))
                continue
            if not isinstance(item, (MessageSourceItem, CallSourceItem)):
                output.append(ModelMessage(seq=item.seq or 0, role="observation", content=as_text(item.content)))
        return sorted(output, key=lambda message: message.seq)

    def assistant(self, seq: int, content: str, batch: list[ToolCall]) -> list[ModelMessage]:
        ordered = sorted(batch, key=lambda call: call.seq)
        calls = [NativeToolCall(id=call.call_id, name=call.name, arguments=call.arguments) for call in ordered]
        result = [ModelMessage(seq=seq, role="assistant", content=content, tool_calls=calls)]
        result.extend(ModelMessage(seq=seq, role="tool", content=as_text(call.output), name=call.name,
                      call_id=call.call_id, status=call.status) for call in ordered)
        return result
