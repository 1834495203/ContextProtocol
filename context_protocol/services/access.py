"""Common transactional reads and explicit DTO projection."""
from datetime import datetime, timezone
from uuid import UUID
from ..models.persistence import (SessionRow, InstanceListRow, InstanceInfo, TemplateRow, TemplateInfo,
    TemplateView, InstanceView, InstanceBinding, BindingRow, HandleRow)
from ..storage.base import UnitOfWork
from .errors import NotFound

def identity(value: str | UUID) -> str:
    return str(UUID(str(value)))

def now() -> datetime:
    return datetime.now(timezone.utc)

def require_template(tx: UnitOfWork, tid: str, *, lock: bool = False) -> TemplateRow:
    row = tx.template(tid, lock=lock)
    if row is None:
        raise NotFound("上下文模板不存在")
    return row

def template_info(row: TemplateInfo) -> TemplateInfo:
    return TemplateInfo(id=row.id, name=row.name, description=row.description, revision=row.revision,
                        created_at=row.created_at, updated_at=row.updated_at)

def template_view(row: TemplateRow) -> TemplateView:
    return TemplateView(template=template_info(row), revision=row.revision, state=row.state)

def list_instance_info(row: InstanceListRow) -> InstanceInfo:
    return InstanceInfo(id=row.id, name=row.name, revision=row.version, created_at=row.created_at,
        slug=row.slug, template_id=row.template_id, template_revision=row.template_revision,
        agent_id=row.agent_id, origin=row.origin)

def instance_info(row: SessionRow, binding: BindingRow | None, handle: HandleRow | None) -> InstanceInfo:
    return InstanceInfo(id=row.id, name=row.name, revision=row.version, created_at=row.created_at,
        slug=handle.slug if handle else None, template_id=binding.template_id if binding else None,
        template_revision=binding.template_revision if binding else None, agent_id=binding.agent_id if binding else None,
        origin=handle.origin if handle else None)

def current_instance(tx: UnitOfWork, sid: str, *, lock: bool = False) -> InstanceView:
    row = tx.session(sid, lock=lock)
    context = tx.context(sid)
    if row is None or context is None:
        raise NotFound("上下文实例不存在")
    binding, handle = tx.binding(sid), tx.handle(sid)
    association = None
    if binding:
        association = InstanceBinding(session_id=sid, template_id=binding.template_id,
            template_revision=binding.template_revision, agent_id=binding.agent_id,
            slug=handle.slug if handle else None, origin=handle.origin if handle else None)
    return InstanceView(session=instance_info(row, binding, handle), revision=row.version,
                        updated_at=context.updated_at, state=context.state, binding=association)
