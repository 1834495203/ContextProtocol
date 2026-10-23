from ...models.commands import GroupUpsertCommand, GroupReorderCommand, GroupDeleteCommand
from .base import UpdateContext, UpdateHandler


class UpsertGroupHandler(UpdateHandler[GroupUpsertCommand]):
    command_type = GroupUpsertCommand
    template_safe = True

    def handle(self, context: UpdateContext, command: GroupUpsertCommand) -> None:
        patch = command.payload
        old = next((group for group in context.state.groups if group.id == patch.id), None)
        group = patch.model_copy(deep=True)
        if old:
            group = old.model_copy(deep=True)
            for name in patch.model_fields_set:
                setattr(group, name, getattr(patch, name))
        if context.actor == "editor" and group.origin != (old.origin if old else "internal"):
            raise ValueError("编辑端不能改变数据来源归属")
        if old:
            context.state.groups[context.state.groups.index(old)] = group
            return
        context.state.groups.append(group)


class ReorderGroupsHandler(UpdateHandler[GroupReorderCommand]):
    command_type = GroupReorderCommand
    template_safe = True

    def handle(self, context: UpdateContext, command: GroupReorderCommand) -> None:
        ids = command.payload.ids
        if len(ids) != len(set(ids)) or set(ids) != {group.id for group in context.state.groups}:
            raise ValueError("排序必须恰好包含当前全部组件 ID")
        lookup = {group.id: group for group in context.state.groups}
        context.state.groups = [lookup[group_id] for group_id in ids]


class DeleteGroupHandler(UpdateHandler[GroupDeleteCommand]):
    command_type = GroupDeleteCommand
    template_safe = True

    def handle(self, context: UpdateContext, command: GroupDeleteCommand) -> None:
        context.component(command.payload.id)
        context.state.groups = [group for group in context.state.groups if group.id != command.payload.id]
