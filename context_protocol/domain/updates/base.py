from typing import Generic, Literal, TypeVar
from ...models.commands import Command
from ...models.context import ContextState, ContentItem, Group, Model

CommandType = TypeVar("CommandType", bound=Command)


class UpdateContext(Model):
    state: ContextState
    seq: int
    turn_id: str | None
    actor: Literal["editor", "producer", "legacy"] = "editor"
    template: bool = False

    def component(self, group_id: str) -> Group:
        group = next((g for g in self.state.groups if g.id == group_id), None)
        if group is None:
            raise ValueError("组件不存在")
        return group

    def writable(self, group_id: str, previous: ContentItem | None = None) -> None:
        group = self.component(group_id)
        if self.actor == "editor" and (group.origin == "external" or (previous and previous.origin == "external")):
            raise ValueError("外部数据只读，只能由数据提供方同步")

    def require_producer(self) -> None:
        if self.actor == "editor":
            raise ValueError("Agent 和工具的外部观测记录只允许提供方写入")


class UpdateHandler(Generic[CommandType]):
    command_type: type[CommandType]
    template_safe: bool = False

    def handle(self, context: UpdateContext, command: CommandType) -> None:
        raise NotImplementedError
