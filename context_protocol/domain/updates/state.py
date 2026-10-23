from ...models.commands import StateSetCommand, StateDeleteCommand, StateDefineCommand
from ...models.context import StateField, StateDefinition
from .base import UpdateContext, UpdateHandler


class SetFieldHandler(UpdateHandler[StateSetCommand]):
    command_type = StateSetCommand
    template_safe = True

    def handle(self, context: UpdateContext, command: StateSetCommand) -> None:
        payload = command.payload
        definition = context.state.field_definitions.get(payload.key)
        if definition is None or context.template:
            definition = StateDefinition(value=payload.value, value_type=payload.value_type, description=payload.description)
            context.state.field_definitions[payload.key] = definition
        context.state.fields[payload.key] = StateField(value=payload.value, value_type=definition.value_type,
                                                       description=definition.description, seq=context.seq)


class DefineFieldHandler(UpdateHandler[StateDefineCommand]):
    command_type = StateDefineCommand
    template_safe = True

    def handle(self, context: UpdateContext, command: StateDefineCommand) -> None:
        payload = command.payload
        definition = StateDefinition(value=payload.value, value_type=payload.value_type, description=payload.description)
        current = context.state.fields.get(payload.key)
        value = definition.value if context.template or current is None else current.value
        field = StateField(value=value, value_type=definition.value_type, description=definition.description, seq=context.seq)
        context.state.field_definitions[payload.key] = definition
        context.state.fields[payload.key] = field


class DeleteFieldHandler(UpdateHandler[StateDeleteCommand]):
    command_type = StateDeleteCommand
    template_safe = True

    def handle(self, context: UpdateContext, command: StateDeleteCommand) -> None:
        context.state.fields.pop(command.payload.key, None)
        context.state.field_definitions.pop(command.payload.key, None)
