"""Typed LangChain lifecycle bridge; mappings exist only at library serialization boundaries."""
import json
from threading import Lock
from typing import Literal
from uuid import uuid4
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, JsonValue
from ..client import ContextClient
from ..models.context import Model, JsonObject, StateToolArguments
from ..models.compilation import CompiledContext, ModelMessage
from ..models.commands import (TraceAppendCommand, AppendTrace, MessageAppendCommand, AppendMessage,
    ToolCallCommand, CallTool, ToolResultCommand, ToolResult, StateSetCommand, SetField,
    BlockUpsertCommand, UpsertBlock, ToolRegisterCommand, RegisterTool)

ALIASES = {"context_state_set": "context.state.set"}
INVERSE_ALIASES = {value: key for key, value in ALIASES.items()}

class LangChainCall(Model):
    id: str
    name: str
    args: JsonObject
    type: Literal["tool_call"] = "tool_call"

class OpenAIFunction(Model):
    name: str
    description: str = ""
    parameters: JsonObject

class OpenAITool(Model):
    type: Literal["function"] = "function"
    function: OpenAIFunction

class ToolError(Model):
    error: str

class FieldUpdated(Model):
    updated: str
    value: JsonValue


def text(value) -> str:
    if isinstance(value, Model):
        value = value.model_dump(mode="json")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)

class LangChainMessageRenderer:
    def __init__(self):
        self.converters = {"system":self.system, "user":self.user, "assistant":self.assistant, "tool":self.tool}
    def system(self, message: ModelMessage):
        return SystemMessage(content=message.content)
    def user(self, message: ModelMessage):
        return HumanMessage(content=message.content)
    def assistant(self, message: ModelMessage):
        calls = [LangChainCall(id=call.id, name=INVERSE_ALIASES.get(call.name,call.name), args=call.arguments)
                 for call in message.tool_calls]
        return AIMessage(content=message.content, tool_calls=[call.model_dump(mode="json") for call in calls])
    def tool(self, message: ModelMessage):
        return ToolMessage(content=message.content, name=INVERSE_ALIASES.get(message.name,message.name),
            tool_call_id=message.call_id, status="error" if message.status == "error" else "success")
    def render(self, artifact: CompiledContext):
        return [self.converters[message.role](message) for message in artifact.model_input.messages]


def render_messages(artifact, state=None):
    typed = artifact if isinstance(artifact, CompiledContext) else CompiledContext.model_validate(artifact)
    return None, LangChainMessageRenderer().render(typed)

class ContextMiddleware(AgentMiddleware):
    def __init__(self, context: ContextClient, budget_chars=24000):
        self.context, self.budget_chars = context, budget_chars
        self.turn_id: str | None = None

    def trace(self, kind: str, loop_id: str, content: str = "", update_id: str | None = None):
        return self.context.submit(TraceAppendCommand(update_id=update_id or str(uuid4()), turn_id=self.turn_id,
            payload=AppendTrace(kind=kind, loop_id=loop_id, content=content)))

    def wrap_model_call(self, request, handler):
        loop_id = str(uuid4())
        self.trace("loop.start", loop_id)
        artifact = self.context.compile(self.budget_chars)
        if artifact.over_budget:
            raise ValueError("Context exceeds the character budget; adjust component policies.")
        _, messages = render_messages(artifact)
        available = {ALIASES.get(tool.name,tool.name): tool for tool in request.tools}
        for section in artifact.envelope.sections:
            if section.truncated and "tools" in section.sources:
                raise ValueError("Native tool schemas cannot be truncated; choose keep/recent/exclude.")
        tools: list[OpenAITool] = []
        for spec in artifact.model_input.tools:
            if spec.name in available:
                tools.append(OpenAITool(function=OpenAIFunction(name=available[spec.name].name,
                    description=spec.description, parameters=spec.input_schema)))
        response = handler(request.override(system_message=None, messages=messages,
                           tools=[tool.model_dump(mode="json") for tool in tools]))
        has_calls = False
        for message in response.result:
            if not isinstance(message, AIMessage):
                continue
            reasoning = message.additional_kwargs.get("reasoning_content") or message.additional_kwargs.get("reasoning")
            if reasoning:
                self.trace("reasoning",loop_id,text(reasoning))
            calls = [LangChainCall.model_validate(call) for call in message.tool_calls]
            has_calls = has_calls or bool(calls)
            receipt = self.context.submit(MessageAppendCommand(update_id=str(uuid4()), turn_id=self.turn_id,
                payload=AppendMessage(role="assistant",content=text(message.content),loop_id=loop_id,
                    phase="tool_request" if calls else "final", tool_call_ids=[call.id for call in calls])))
            for call in calls:
                self.context.submit(ToolCallCommand(update_id=str(uuid4()), turn_id=self.turn_id,
                    payload=CallTool(call_id=call.id,name=ALIASES.get(call.name,call.name),runtime_name=call.name,
                                     arguments=call.args,assistant_seq=receipt.seq,loop_id=loop_id)))
        if not has_calls:
            self.end_loop(loop_id)
        return response

    def end_loop(self, loop_id):
        self.trace("loop.end", loop_id, update_id="loop-end-"+loop_id)

    def wrap_tool_call(self, request, handler):
        call = LangChainCall.model_validate(request.tool_call)
        try:
            result = handler(request)
        except Exception as exc:
            result = ToolMessage(content=text(ToolError(error=type(exc).__name__)), tool_call_id=call.id,
                                 name=call.name, status="error")
        if not isinstance(result, ToolMessage):
            raise TypeError("This adapter supports ToolMessage tools, not graph Commands.")
        self.context.submit(ToolResultCommand(update_id=str(uuid4()), turn_id=self.turn_id,
            payload=ToolResult(call_id=call.id,output=result.content,is_error=result.status=="error")))
        calls = self.context.view().state.calls
        loop_id = calls[call.id].loop_id
        if loop_id and all(item.status != "pending" for item in calls.values() if item.loop_id == loop_id):
            self.end_loop(loop_id)
        return result

class StateArguments(StateToolArguments):
    key: str = Field(description="自定义字段名")
    value: JsonValue = Field(description="新的字段值")

class ContextAgent:
    def __init__(self, context: ContextClient, model, budget_chars=24000, *, tools=None, instructions=None):
        self.context, self._turn_lock = context, Lock()
        self.middleware = ContextMiddleware(context,budget_chars)
        def set_state(key: str, value: JsonValue, value_type="json", description=""):
            context.submit(StateSetCommand(update_id=str(uuid4()), turn_id=self.middleware.turn_id,
                payload=SetField(key=key,value=value,value_type=value_type,description=description)))
            return text(FieldUpdated(updated=key,value=value))
        tools = list(tools or [])+[StructuredTool.from_function(set_state,name="context_state_set",args_schema=StateArguments,
                   description="创建或更新上下文的自定义状态字段。")]
        current = context.view().state
        target = next((group for group in current.groups if group.role=="system" and "blocks" in group.sources),None)
        if instructions and target and "agent_instructions" not in current.blocks:
            context.submit(BlockUpsertCommand(update_id=str(uuid4()),payload=UpsertBlock(id="agent_instructions",group_id=target.id,content=instructions)))
        for tool in tools:
            if tool.name == "context_state_set":
                continue
            spec = OpenAITool.model_validate(convert_to_openai_tool(tool)).function
            context.submit(ToolRegisterCommand(update_id=str(uuid4()),payload=RegisterTool(name=spec.name,
                           description=spec.description,input_schema=spec.parameters)))
        self.agent = create_agent(model=model,tools=tools,middleware=[self.middleware])

    def run(self, prompt):
        with self._turn_lock:
            self.middleware.turn_id=str(uuid4())
            self.context.submit(MessageAppendCommand(update_id=str(uuid4()),turn_id=self.middleware.turn_id,
                                                     payload=AppendMessage(role="user",content=prompt)))
            # LangGraph's public API requires a mapping containing native message objects.
            result=self.agent.invoke({"messages":[HumanMessage(content=prompt)]},config={"recursion_limit":20})
            return result["messages"][-1].content
