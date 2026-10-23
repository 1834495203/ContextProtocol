"""Instance lifecycle, stable template/slug addressing and ownership metadata."""
import re
from uuid import uuid4
from ..models.context import ContextState
from ..models.persistence import (InstanceInfo, InstanceView, ConnectionResult, SessionRow, ContextRow,
                                  BindingRow, HandleRow, TemplateRow)
from ..storage.base import ContextRepository, UnitOfWork
from .access import identity, now, current_instance, require_template, instance_info, list_instance_info
from .errors import Conflict, NotFound


def reset_sequences(state: ContextState) -> ContextState:
    result = state.model_copy(deep=True)
    for collection in (result.blocks, result.items, result.fields, result.tools):
        for item in collection.values():
            item.seq = 0
    return result

class InstanceService:
    def __init__(self, repository: ContextRepository):
        self.repository = repository

    def list(self, template_id: str | None = None, *, unassigned: bool = False) -> list[InstanceInfo]:
        with self.repository.transaction() as tx:
            if template_id:
                require_template(tx, identity(template_id))
            return [list_instance_info(row) for row in tx.sessions(template_id=template_id, unassigned=unassigned)]

    def current(self, sid: str) -> InstanceView:
        with self.repository.transaction() as tx:
            return current_instance(tx, identity(sid), lock=True)

    @staticmethod
    def validate_slug(slug: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", slug):
            raise ValueError("实例简名须以小写字母开头，仅包含小写字母、数字、下划线或连字符，最长 64 字符")
        return slug

    def create(self, name: str, *, template_id: str | None = None, agent_id: str | None = None,
               slug: str | None = None) -> InstanceInfo:
        with self.repository.transaction(write=True) as tx:
            if template_id is None:
                templates = tx.templates()
                if not templates:
                    raise ValueError("请先创建上下文模板")
                template_id = templates[0].id
            template = require_template(tx, identity(template_id), lock=True)
            return self._create(tx, name, template, agent_id, slug)

    def _create(self, tx: UnitOfWork, name: str, template: TemplateRow, agent_id: str | None,
                slug: str | None) -> InstanceInfo:
        sid = str(uuid4())
        slug = self.validate_slug(slug) if slug is not None else "ctx-"+sid.replace("-", "")
        if tx.named_instance(template.id, slug):
            raise Conflict("该模板下已有同名实例，请使用接入接口继续使用或更换简名")
        row = SessionRow(id=sid, name=name, version=0, created_at=now())
        binding = BindingRow(session_id=sid, template_id=template.id, template_revision=template.revision, agent_id=agent_id)
        handle = HandleRow(session_id=sid, template_id=template.id, slug=slug, origin="created")
        tx.insert_session(row)
        tx.save_context(ContextRow(session_id=sid, state=reset_sequences(template.state), updated_at=now()))
        tx.insert_binding(binding)
        tx.insert_handle(handle)
        return instance_info(row, binding, handle)

    def named(self, tid: str, slug: str) -> InstanceView:
        with self.repository.transaction() as tx:
            require_template(tx, identity(tid))
            handle = tx.named_instance(identity(tid), self.validate_slug(slug))
            if handle is None:
                raise NotFound("该模板下的实例不存在")
            return current_instance(tx, handle.session_id, lock=True)

    def connect(self, tid: str, slug: str, *, name: str | None = None, agent_id: str | None = None,
                create: bool = True) -> ConnectionResult:
        with self.repository.transaction(write=True) as tx:
            template = require_template(tx, identity(tid), lock=True)
            slug = self.validate_slug(slug)
            handle = tx.named_instance(template.id, slug)
            if handle:
                current = current_instance(tx, handle.session_id, lock=True)
                existing_agent = current.binding.agent_id
                if agent_id and existing_agent and agent_id != existing_agent:
                    raise Conflict("实例的 Agent 标记不同，请检查简名或使用另一个实例")
                return ConnectionResult(instance=current.session, created=False)
            if not create:
                raise NotFound("该模板下的实例不存在；create=false 不会创建实例")
            return ConnectionResult(instance=self._create(tx, name or slug, template, agent_id, slug), created=True)

    def assign(self, sid: str, tid: str, slug: str, *, agent_id: str | None = None) -> InstanceView:
        with self.repository.transaction(write=True) as tx:
            template = require_template(tx, identity(tid), lock=True)
            current = current_instance(tx, identity(sid), lock=True)
            if current.binding:
                raise Conflict("实例已归属模板，请在实例信息中修改简名")
            slug = self.validate_slug(slug)
            if tx.named_instance(template.id, slug):
                raise Conflict("该模板下已有同名实例")
            tx.insert_binding(BindingRow(session_id=identity(sid), template_id=template.id,
                template_revision=template.revision, agent_id=agent_id))
            tx.insert_handle(HandleRow(session_id=identity(sid), template_id=template.id, slug=slug, origin="assigned"))
            return current_instance(tx, identity(sid))

    def rename(self, sid: str, name: str, slug: str, agent_id: str | None, expected_revision: int) -> InstanceView:
        with self.repository.transaction(write=True) as tx:
            binding = tx.binding(identity(sid))
            if binding is None:
                raise ValueError("旧实例尚未归属模板，请先设置归属")
            require_template(tx, binding.template_id, lock=True)
            current = current_instance(tx, identity(sid), lock=True)
            if current.revision != expected_revision:
                raise Conflict("实例已被修改，请刷新后重试")
            slug = self.validate_slug(slug)
            taken = tx.named_instance(binding.template_id, slug)
            if taken and taken.session_id != identity(sid):
                raise Conflict("该模板下已有同名实例")
            tx.save_instance_metadata(identity(sid), name, slug, agent_id)
            tx.set_version(identity(sid), current.revision+1)
            return current_instance(tx, identity(sid))
