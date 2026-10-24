"""Framework-independent descriptions for optional context capabilities."""
from pydantic import Field, JsonValue
from .commands import SetField, AppendMemory
from .context import RegisteredTool, Model


class ContextToolCatalog(Model):
    provider_id: str = 'context'
    registration_required: bool = True
    tools: list[RegisteredTool]


class StateArguments(SetField):
    key: str = Field(min_length=1, max_length=200, description='需要创建或更新的状态字段名')
    value: JsonValue = Field(description='字段的新值，必须符合 value_type 声明的类型')


class MemoryArguments(AppendMemory):
    content: str = Field(min_length=1, description='需要长期保留的事实或结论')
    kind: str = Field(default='long_term', description='记忆分类')
    source_uri: str | None = Field(default=None, description='可选的来源地址')


def context_tool_catalog() -> list[RegisteredTool]:
    return [
        RegisteredTool(name='context.state.set', model_name='context_state_set',
            description='创建或更新当前上下文实例的状态字段。', input_schema=StateArguments.model_json_schema()),
        RegisteredTool(name='context.memory.append', model_name='context_memory_append',
            description='将需要跨轮次保留的事实或结论写入当前上下文实例的长期记忆。',
            input_schema=MemoryArguments.model_json_schema())
    ]
