"""Typed LangChain lifecycle bridge; mappings exist only at library serialization boundaries."""
import asyncio
import json
import inspect
from threading import Lock
from typing import Literal
from uuid import uuid4
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from ..client import ContextClient
from ..models.context import Model, JsonObject, RegisteredTool
from ..tools import HostTools, LocalTool, validate_tool_arguments
from ..models.compilation import CompiledContext, ModelMessage
from ..gateway_agent import GatewayAgent as BaseGatewayAgent, ModelReply
from ..models.host import PrepareInput
from ..models.commands import (TraceAppendCommand, AppendTrace, MessageAppendCommand, AppendMessage,
    ToolCallCommand, CallTool, ToolResultCommand, ToolResult,
    BlockUpsertCommand, UpsertBlock, ToolRegisterCommand, RegisterTool)

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

def text(value) -> str:
    if isinstance(value, Model):
        value = value.model_dump(mode="json")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def local_tool_adapter(local: LocalTool):
    definition = local.definition
    options = {'name': definition.model_name or definition.name,
               'description': definition.description, 'args_schema': definition.input_schema.root}
    if inspect.iscoroutinefunction(local.handler):
        async def invoke(**arguments):
            validate_tool_arguments(definition, arguments)
            return await local.handler(**arguments)
        return StructuredTool.from_function(coroutine=invoke, **options)
    def invoke(**arguments):
        validate_tool_arguments(definition, arguments)
        return local.handler(**arguments)
    return StructuredTool.from_function(func=invoke, **options)

class LangChainMessageRenderer:
    def __init__(self, names=None):
        self.names = names or {}
        self.converters = {"system":self.system, "user":self.user, "assistant":self.assistant, "tool":self.tool}
    def system(self, message: ModelMessage):
        return SystemMessage(content=message.content)
    def user(self, message: ModelMessage):
        return HumanMessage(content=message.content)
    def assistant(self, message: ModelMessage):
        calls = [LangChainCall(id=call.id, name=self.names.get(call.name,call.name), args=call.arguments)
                 for call in message.tool_calls]
        return AIMessage(content=message.content, tool_calls=[call.model_dump(mode="json") for call in calls])
    def tool(self, message: ModelMessage):
        return ToolMessage(content=message.content, name=self.names.get(message.name,message.name),
            tool_call_id=message.call_id, status="error" if message.status == "error" else "success")
    def render(self, artifact: CompiledContext):
        return [self.converters[message.role](message) for message in artifact.model_input.messages]


def render_messages(artifact, state=None, *, names=None):
    typed = artifact if isinstance(artifact, CompiledContext) else CompiledContext.model_validate(artifact)
    return None, LangChainMessageRenderer(names).render(typed)

class ContextMiddleware(AgentMiddleware):
    def __init__(self, context: ContextClient, budget_chars=24000, *, host_tools=None, protocol_names=None):
        self.context, self.budget_chars = context, budget_chars
        self.host_tools = host_tools
        self.protocol_names = protocol_names or {}
        self.prepared = None
        self.turn_id: str | None = None

    def trace(self, kind: str, loop_id: str, content: str = "", update_id: str | None = None):
        return self.context.submit(TraceAppendCommand(update_id=update_id or str(uuid4()), turn_id=self.turn_id,
            payload=AppendTrace(kind=kind, loop_id=loop_id, content=content)))

    def prepare(self):
        loop_id = str(uuid4())
        self.trace("loop.start", loop_id)
        prepared = self.host_tools.prepare(self.budget_chars) if self.host_tools else self.context.prepare(PrepareInput(budget_chars=self.budget_chars))
        if not prepared.ready:
            raise ValueError("; ".join(issue.message for issue in prepared.issues))
        self.prepared = prepared
        return loop_id, prepared.compiled

    def model_request(self, request, artifact):
        _, messages = render_messages(artifact, names={v:k for k,v in self.protocol_names.items()})
        available = {tool.name: tool for tool in request.tools}
        for tool in request.tools:
            available.setdefault(self.protocol_names.get(tool.name, tool.name), tool)
        tools: list[OpenAITool] = []
        for spec in artifact.model_input.tools:
            if spec.name in available:
                tools.append(OpenAITool(function=OpenAIFunction(name=available[spec.name].name,
                    description=spec.description, parameters=spec.input_schema)))
        return request.override(system_message=None, messages=messages,
                                tools=[tool.model_dump(mode="json") for tool in tools])

    def record_response(self, messages, loop_id):
        has_calls = False
        for message in messages:
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
                    payload=CallTool(call_id=call.id,name=call.name if self.host_tools else self.protocol_names.get(call.name,call.name),runtime_name=call.name,
                                     arguments=call.args,assistant_seq=receipt.seq,loop_id=loop_id)))
        if not has_calls:
            self.end_loop(loop_id)

    def record_tool_result(self, call, result):
        if not isinstance(result, ToolMessage):
            raise TypeError("This adapter supports ToolMessage tools, not graph Commands.")
        self.context.submit(ToolResultCommand(update_id=str(uuid4()), turn_id=self.turn_id,
            payload=ToolResult(call_id=call.id,output=result.content,is_error=result.status=="error")))
        calls = self.context.view().state.calls
        loop_id = calls[call.id].loop_id
        if loop_id and all(item.status != "pending" for item in calls.values() if item.loop_id == loop_id):
            self.end_loop(loop_id)

    def wrap_model_call(self, request, handler):
        loop_id, artifact = self.prepare()
        response = handler(self.model_request(request, artifact))
        self.record_response(response.result, loop_id)
        return response

    async def awrap_model_call(self, request, handler):
        loop_id, artifact = await asyncio.to_thread(self.prepare)
        response = await handler(self.model_request(request, artifact))
        await asyncio.to_thread(self.record_response, response.result, loop_id)
        return response

    def end_loop(self, loop_id):
        self.trace("loop.end", loop_id, update_id="loop-end-"+loop_id)

    def wrap_tool_call(self, request, handler):
        call = LangChainCall.model_validate(request.tool_call)
        try:
            if self.host_tools:
                self.host_tools.check_call(self.prepared, call.name)
            result = handler(request)
        except Exception as exc:
            result = ToolMessage(content=text(ToolError(error=type(exc).__name__)), tool_call_id=call.id,
                                 name=call.name, status="error")
        self.record_tool_result(call, result)
        return result

    async def awrap_tool_call(self, request, handler):
        call = LangChainCall.model_validate(request.tool_call)
        try:
            if self.host_tools:
                await asyncio.to_thread(self.host_tools.check_call, self.prepared, call.name)
            result = await handler(request)
        except Exception as exc:
            result = ToolMessage(content=text(ToolError(error=type(exc).__name__)), tool_call_id=call.id,
                                 name=call.name, status="error")
        await asyncio.to_thread(self.record_tool_result, call, result)
        return result

class ContextAgent:
    def __init__(self, context: ContextClient, model, budget_chars=24000, *, tools=None, instructions=None,
                 host_id: str | None = None, provider_id: str = "local",
                 tool_providers: dict[str, list[LocalTool]] | None = None):
        self.context, self._turn_lock = context, Lock()
        self._async_turn_lock = asyncio.Lock()
        providers = tool_providers or {}
        if provider_id in providers:
            raise ValueError('Use a distinct provider ID for native framework tools')
        native_tools = list(tools or [])
        all_tools = list(native_tools)
        protocol_names = {}
        for provider in providers.values():
            for local in provider:
                definition = local.definition
                runtime_name = definition.model_name or definition.name
                protocol_names[runtime_name] = definition.name
                all_tools.append(local_tool_adapter(local))
        if len({tool.name for tool in all_tools}) != len(all_tools):
            raise ValueError('Duplicate model tool name; assign distinct model_name values')
        runtime_names = {tool.name for tool in all_tools}
        if any(protocol != runtime and protocol in runtime_names for runtime, protocol in protocol_names.items()):
            raise ValueError('Tool alias conflicts with another tool identity; choose distinct names')
        self.host_tools = HostTools(context, host_id) if host_id else None
        self.middleware = ContextMiddleware(context,budget_chars,host_tools=self.host_tools,protocol_names=protocol_names)
        current = context.view().state
        target = next((group for group in current.groups if group.role=="system" and "blocks" in group.sources),None)
        if instructions and target and "agent_instructions" not in current.blocks:
            context.submit(BlockUpsertCommand(update_id=str(uuid4()),payload=UpsertBlock(id="agent_instructions",group_id=target.id,content=instructions)))
        registered = []
        for tool in native_tools:
            if self.host_tools:
                spec = OpenAITool.model_validate(convert_to_openai_tool(tool)).function
                registered.append(RegisteredTool(name=spec.name, description=spec.description,
                                                 input_schema=spec.parameters))
                continue
            spec = OpenAITool.model_validate(convert_to_openai_tool(tool)).function
            context.submit(ToolRegisterCommand(update_id=str(uuid4()),payload=RegisterTool(name=spec.name,
                           description=spec.description,input_schema=spec.parameters)))
        if self.host_tools:
            self.host_tools.sync(provider_id, registered, kind='langchain')
            for pid, provider in providers.items():
                self.host_tools.register_provider(pid, provider)
        else:
            for pid, provider in providers.items():
                for local in provider:
                    definition = local.definition
                    context.submit(ToolRegisterCommand(update_id=str(uuid4()), payload=RegisterTool(name=definition.name,
                        description=definition.description, input_schema=definition.input_schema,
                        tool_ref=pid+'/'+definition.name)))
        self.agent = create_agent(model=model,tools=all_tools,middleware=[self.middleware])

    def close(self):
        if self.host_tools:
            self.host_tools.close()

    def run(self, prompt):
        with self._turn_lock:
            self.middleware.turn_id=str(uuid4())
            self.context.submit(MessageAppendCommand(update_id=str(uuid4()),turn_id=self.middleware.turn_id,
                                                     payload=AppendMessage(role="user",content=prompt)))
            # LangGraph's public API requires a mapping containing native message objects.
            result=self.agent.invoke({"messages":[HumanMessage(content=prompt)]},config={"recursion_limit":20})
            return result["messages"][-1].content


    async def arun(self, prompt):
        """Async turn runner required by host-side coroutine tools."""
        async with self._async_turn_lock:
            self.middleware.turn_id=str(uuid4())
            append=MessageAppendCommand(update_id=str(uuid4()),turn_id=self.middleware.turn_id,
                                        payload=AppendMessage(role="user",content=prompt))
            await asyncio.to_thread(self.context.submit, append)
            result=await self.agent.ainvoke({"messages":[HumanMessage(content=prompt)]},config={"recursion_limit":20})
            return result["messages"][-1].content


class GatewayAgent(BaseGatewayAgent):
    """LangChain model adapter; tools and their execution remain on the server."""
    def __init__(self, context: ContextClient, model, *, max_steps=20):
        def invoke(model_input):
            renderer = LangChainMessageRenderer()
            messages = [renderer.converters[message.role](message) for message in model_input.messages]
            tools = [OpenAITool(function=OpenAIFunction(name=spec.name, description=spec.description,
                     parameters=spec.input_schema)).model_dump(mode='json') for spec in model_input.tools]
            response = (model.bind_tools(tools) if tools else model).invoke(messages)
            return ModelReply(content=text(response.content), tool_calls=[
                {'id': call['id'], 'name': call['name'], 'arguments': call['args']} for call in response.tool_calls])
        super().__init__(context, invoke, max_steps=max_steps)
