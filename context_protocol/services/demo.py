"""Small typed example data, separate from HTTP controllers and business services."""
from uuid import uuid4
from ..models.context import Model, JsonObject
from ..models.commands import (ContextUpdate, Command, BlockUpsertCommand, UpsertBlock, StateSetCommand, SetField,
    ToolRegisterCommand, RegisterTool, MessageAppendCommand, AppendMessage, ToolCallCommand, CallTool,
    ToolResultCommand, ToolResult, MemoryAppendCommand, AppendMemory)

class SearchArguments(Model):
    query: str

class SearchMatch(Model):
    source: str
    content: str

class SearchOutput(Model):
    matches: list[SearchMatch]


def demo_updates() -> list[ContextUpdate]:
    commands: list[Command] = [
        BlockUpsertCommand(update_id=str(uuid4()), payload=UpsertBlock(id="persona", group_id="instructions", content="你是一名严谨的中文研究助手，区分事实与推断，保留资料来源。")),
        BlockUpsertCommand(update_id=str(uuid4()), payload=UpsertBlock(id="goal", group_id="goals", content="设计独立于 Agent 框架的上下文管理协议，支持组件、状态与动态编译。")),
        StateSetCommand(update_id=str(uuid4()), payload=SetField(key="current_phase", value="协议设计", value_type="string")),
        StateSetCommand(update_id=str(uuid4()), payload=SetField(key="language", value="zh-CN", value_type="string")),
        ToolRegisterCommand(update_id=str(uuid4()), payload=RegisterTool(name="documents.search", description="检索本地项目文档。", input_schema=JsonObject(SearchArguments.model_json_schema()))),
        MessageAppendCommand(update_id=str(uuid4()), turn_id="turn-1", payload=AppendMessage(role="user", content="帮我设计通用上下文协议，工具、状态和对话要能分组编排。")),
        ToolCallCommand(update_id=str(uuid4()), turn_id="turn-1", payload=CallTool(call_id="search-001", name="documents.search", arguments=JsonObject(SearchArguments(query="上下文协议范围").model_dump()))),
        ToolResultCommand(update_id=str(uuid4()), turn_id="turn-1", payload=ToolResult(call_id="search-001", output=SearchOutput(matches=[SearchMatch(source="requirements.md", content="第一版聚焦分组编译、可视化与持久化。")]).model_dump())),
        MessageAppendCommand(update_id=str(uuid4()), turn_id="turn-1", payload=AppendMessage(role="assistant", content="以可扩展组件管理默认配置，宿主同步对话与工具结果，编译器生成模型输入。")),
        MemoryAppendCommand(update_id=str(uuid4()), payload=AppendMemory(content="本版暂不执行外部工具或向量检索。")),
    ]
    for command in commands:
        command.source = "demo"
    return [ContextUpdate(command) for command in commands]
