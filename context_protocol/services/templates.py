"""Reusable configuration lifecycle, isolated from mutable runtime observations."""
from uuid import uuid4
from ..models.context import ContextState, StateField, StateDefinition, ValueType, new_state
from ..models.commands import ContextUpdate
from ..models.persistence import TemplateInfo, TemplateRow, TemplateView, ContextRow
from ..storage.base import ContextRepository
from ..domain.updates.registry import UpdateRegistry
from ..domain.updates.base import UpdateContext
from .access import identity, now, current_instance, require_template, template_view
from .instances import reset_sequences
from .errors import Conflict
from .template_sync import TemplateSynchronizer

DEFAULT_VALUES = {
    ValueType.JSON: lambda: None,
    ValueType.STRING: str,
    ValueType.NUMBER: int,
    ValueType.BOOLEAN: bool,
    ValueType.OBJECT: dict,
    ValueType.ARRAY: list,
}

class TemplateService:
    def __init__(self, repository: ContextRepository, updates: UpdateRegistry):
        self.repository, self.updates = repository, updates

    def list(self) -> list[TemplateInfo]:
        with self.repository.transaction() as tx:
            return tx.templates()

    def get(self, tid: str) -> TemplateView:
        with self.repository.transaction() as tx:
            return template_view(require_template(tx, identity(tid), lock=True))

    def create(self, name: str, description: str = "", *, source_template_id: str | None = None,
               source_context_id: str | None = None) -> TemplateView:
        if source_template_id and source_context_id:
            raise ValueError("只能选择一个模板来源")
        with self.repository.transaction(write=True) as tx:
            state = new_state()
            if source_template_id:
                state = require_template(tx, identity(source_template_id), lock=True).state.model_copy(deep=True)
            if source_context_id:
                source = current_instance(tx, identity(source_context_id), lock=True).state
                state = self.extract_configuration(source)
            row = TemplateRow(id=str(uuid4()), name=name, description=description, revision=0,
                              state=reset_sequences(state), created_at=now(), updated_at=now())
            tx.insert_template(row)
            return template_view(row)

    @staticmethod
    def extract_configuration(source: ContextState) -> ContextState:
        state = new_state()
        state.groups = [group.model_copy(deep=True) for group in source.groups]
        state.tool_prompts = {key: prompt.model_copy(deep=True) for key, prompt in source.tool_prompts.items()}
        state.tool_sources = list(source.tool_sources) if source.tool_sources is not None else None
        origins = {group.id: group.origin for group in source.groups}
        state.blocks = {key: item.model_copy(deep=True) for key, item in source.blocks.items()
                        if item.origin == "internal" and origins.get(item.group_id, "internal") == "internal"}
        state.items = {key: item.model_copy(deep=True) for key, item in source.items.items()
                       if item.origin == "internal" and origins.get(item.group_id, "internal") == "internal"}
        state.fields = {key: StateField(value_type=value.value_type, description=value.description,
                        value=DEFAULT_VALUES[value.value_type]()) for key, value in source.fields.items()}
        state.field_definitions = {key: StateDefinition(value=value.value, value_type=value.value_type,
                                   description=value.description) for key, value in state.fields.items()}
        return state

    def update(self, tid: str, update: ContextUpdate, expected_revision: int | None = None) -> TemplateView:
        command = update.root
        expected = expected_revision if expected_revision is not None else command.expected_revision
        with self.repository.transaction(write=True) as tx:
            row = require_template(tx, identity(tid), lock=True)
            if expected is not None and expected != row.revision:
                raise Conflict("模板已被修改，请刷新后重试")
            before = row.state.model_copy(deep=True)
            row.revision += 1
            self.updates.apply(UpdateContext(state=row.state, seq=row.revision, turn_id=command.turn_id), command, template=True)
            row.updated_at = now()
            tx.save_template(row)
            # Lock order is always template, then instances in stable ID order.
            for instance in sorted(tx.sessions(template_id=row.id), key=lambda instance: instance.id):
                current = current_instance(tx, instance.id, lock=True)
                revision = current.revision + 1
                TemplateSynchronizer().apply(before, row.state, current.state, revision)
                tx.save_context(ContextRow(session_id=instance.id, state=current.state, updated_at=now()))
                tx.set_version(instance.id, revision)
                binding = tx.binding(instance.id)
                binding.template_revision = row.revision
                tx.save_binding(binding)
            return template_view(row)

    def rename(self, tid: str, name: str, description: str, expected_revision: int) -> TemplateView:
        with self.repository.transaction(write=True) as tx:
            row = require_template(tx, identity(tid), lock=True)
            if expected_revision != row.revision:
                raise Conflict("模板已被修改，请刷新后重试")
            row.name, row.description = name, description
            row.revision += 1
            row.updated_at = now()
            tx.save_template(row)
            return template_view(row)
