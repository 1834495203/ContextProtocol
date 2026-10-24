"""Apply configuration deltas atomically without copying runtime observations."""
from typing import TypeVar
from pydantic import ValidationError
from ..models.context import ContextState, ContentItem, Model, StateField
from .errors import Conflict

Record = TypeVar("Record", bound=Model)


def merge_records(before: dict[str, Record], after: dict[str, Record], target: dict[str, Record]) -> None:
    for key in before.keys() - after.keys():
        previous = target.get(key)
        if isinstance(previous, ContentItem) and previous.origin == "external":
            raise Conflict(f"模板内容 {key} 与实例外部只读内容冲突")
        target.pop(key, None)
    for key, record in after.items():
        if before.get(key) != record:
            previous = target.get(key)
            if isinstance(previous, ContentItem) and previous.origin == "external":
                raise Conflict(f"模板内容 {key} 与实例外部只读内容冲突")
            target[key] = record.model_copy(deep=True)


class TemplateSynchronizer:
    def apply(self, before: ContextState, after: ContextState, target: ContextState, revision: int) -> None:
        if before.tool_sources != after.tool_sources:
            target.tool_sources = list(after.tool_sources) if after.tool_sources is not None else None
        merge_records(before.blocks, after.blocks, target.blocks)
        merge_records(before.items, after.items, target.items)
        merge_records(before.tool_prompts, after.tool_prompts, target.tool_prompts)
        old_groups = {g.id: g for g in before.groups}
        new_groups = {g.id: g for g in after.groups}
        groups = {g.id: g for g in target.groups}
        for key, group in new_groups.items():
            previous = groups.get(key)
            if old_groups.get(key) != group and previous and previous.origin != group.origin:
                raise Conflict(f"组件 {key} 的模板来源归属与实例不同")
        merge_records(old_groups, new_groups, groups)
        for collection in (target.blocks, target.items):
            for key, item in collection.items():
                group = groups.get(item.group_id)
                if group and group.origin == "external" and item.origin == "internal":
                    changed = after.blocks.get(key) != before.blocks.get(key) or after.items.get(key) != before.items.get(key)
                    if changed:
                        raise Conflict(f"模板内容 {key} 的实例组件属于外部只读来源")
        # Preserve local slots; only template-owned slots follow the template order.
        order_changed = list(old_groups) != list(new_groups)
        shared = iter(g.id for g in after.groups if g.id in groups)
        order = [g.id for g in target.groups if g.id in groups]
        order.extend(key for key in groups if key not in order)
        if order_changed:
            order = [next(shared) if key in new_groups else key for key in order]
        target.groups = [groups[key] for key in order]
        for key in before.field_definitions.keys() - after.field_definitions.keys():
            target.fields.pop(key, None)
            target.field_definitions.pop(key, None)
        for key, definition in after.field_definitions.items():
            if before.field_definitions.get(key) == definition:
                continue
            current = target.fields.get(key)
            try:
                field = StateField(value=current.value if current else definition.value,
                                   value_type=definition.value_type, description=definition.description, seq=revision)
            except ValidationError as exc:
                raise Conflict(f"字段 {key} 的新类型与实例当前值不兼容，请先调整实例值") from exc
            target.field_definitions[key] = definition.model_copy(deep=True)
            target.fields[key] = field
