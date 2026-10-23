"""Command dispatch. Adding a handler does not alter the execution algorithm."""
from typing import Any
from ...models.commands import Command
from .base import UpdateContext, UpdateHandler
from .components import UpsertGroupHandler, ReorderGroupsHandler, DeleteGroupHandler
from .content import UpsertBlockHandler, DeleteBlockHandler, UpsertItemHandler, DeleteItemHandler
from .state import SetFieldHandler, DeleteFieldHandler, DefineFieldHandler
from .observations import (RegisterToolHandler, UnregisterToolHandler, AppendMessageHandler, EditMessageHandler,
                           CallToolHandler, ToolResultHandler, AppendMemoryHandler, AppendTraceHandler)


class UpdateRegistry:
    def __init__(self):
        self.handlers: dict[type[Command], UpdateHandler[Any]] = {}

    def register(self, handler: UpdateHandler) -> None:
        if handler.command_type in self.handlers:
            raise ValueError("更新处理器已注册")
        self.handlers[handler.command_type] = handler

    def apply(self, context: UpdateContext, command: Command, *, template: bool = False) -> None:
        handler = self.handlers.get(type(command))
        if handler is None:
            raise ValueError("未注册更新处理器")
        if template and not handler.template_safe:
            raise ValueError("模板只保存组成、默认内容与字段默认值；运行数据请写入上下文实例")
        context.template = template
        handler.handle(context, command)


def default_registry() -> UpdateRegistry:
    registry = UpdateRegistry()
    for handler_type in (UpsertGroupHandler, ReorderGroupsHandler, DeleteGroupHandler, UpsertBlockHandler,
        DeleteBlockHandler, UpsertItemHandler, DeleteItemHandler, SetFieldHandler, DeleteFieldHandler, DefineFieldHandler,
        RegisterToolHandler, UnregisterToolHandler, AppendMessageHandler, EditMessageHandler,
        CallToolHandler, ToolResultHandler, AppendMemoryHandler, AppendTraceHandler):
        registry.register(handler_type())
    return registry
