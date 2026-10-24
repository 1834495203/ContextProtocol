"""Small framework-independent loop whose only tool executor is the Web service."""
from threading import Lock
from uuid import uuid4
from pydantic import Field
from .client import ContextClient
from .models.context import Model
from .models.compilation import NativeToolCall
from .models.gateway import GatewayCallInput


class ModelReply(Model):
    content: str = ''
    tool_calls: list[NativeToolCall] = Field(default_factory=list)


class GatewayAgent:
    def __init__(self, context: ContextClient, model, *, max_steps=20):
        self.context, self.model, self.max_steps = context, model, max_steps
        self._lock = Lock()

    def run(self, prompt: str):
        with self._lock:
            turn_id = str(uuid4())
            self.context.emit('message.append', {'role': 'user', 'content': prompt}, turn_id)
            for _ in range(self.max_steps):
                prepared = self.context.prepare()
                if not prepared.ready:
                    raise ValueError('; '.join(issue.message for issue in prepared.issues))
                if prepared.execution_mode != 'gateway':
                    raise ValueError('Select central tool sources before using GatewayAgent')
                reply = ModelReply.model_validate(self.model(prepared.compiled.model_input))
                receipt = self.context.emit('message.append', {'role': 'assistant', 'content': reply.content,
                    'tool_call_ids': [call.id for call in reply.tool_calls]}, turn_id)
                if not reply.tool_calls:
                    return reply.content
                for call in reply.tool_calls:
                    self.context.call_tool(GatewayCallInput(preparation_id=prepared.preparation_id, call_id=call.id,
                        name=call.name, arguments=call.arguments, assistant_seq=receipt['seq'], turn_id=turn_id))
            raise RuntimeError('Agent step limit reached')
