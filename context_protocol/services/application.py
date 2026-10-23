"""Composition of focused application services; no SQL or HTTP logic."""
from ..storage.base import ContextRepository
from ..models.commands import ContextUpdate
from ..models.persistence import (InstanceInfo, InstanceView, ConnectionResult, TemplateInfo, TemplateView,
                                  UpdateResult, StorageLocation)
from ..models.compilation import CompiledContext
from ..domain.compiler import ContextCompiler
from ..domain.updates.registry import default_registry
from .bootstrap import BootstrapService
from .instances import InstanceService
from .templates import TemplateService
from .updates import UpdateService
from .compilation import CompilationService, InstanceExport, TemplateExport

class ContextApplication:
    def __init__(self, repository: ContextRepository, *, compiler: ContextCompiler | None = None):
        self.repository = repository
        registry = default_registry()
        self.bootstrap = BootstrapService(repository)
        self.instances = InstanceService(repository)
        self.template_service = TemplateService(repository, registry)
        self.mutations = UpdateService(repository, registry)
        self.compilation = CompilationService(self.instances, self.template_service, compiler or ContextCompiler())

    @property
    def backend(self) -> str:
        return self.repository.backend
    def initialize(self) -> None:
        self.bootstrap.initialize()
    def health(self) -> None:
        self.repository.health()
    def describe(self) -> StorageLocation:
        return self.repository.describe()
    def close(self) -> None:
        self.repository.close()
    def sessions(self) -> list[InstanceInfo]:
        return self.instances.list()
    def unassigned_instances(self) -> list[InstanceInfo]:
        return self.instances.list(unassigned=True)
    def create_session(self, name: str, *, template_id: str | None = None, agent_id: str | None = None,
                       slug: str | None = None) -> InstanceInfo:
        return self.instances.create(name, template_id=template_id, agent_id=agent_id, slug=slug)
    def create_demo_session(self, name: str) -> InstanceInfo:
        from .demo import demo_updates
        instance = self.create_session(name)
        for update in demo_updates():
            self.update(instance.id, update, actor="producer")
        return self.current(instance.id).session
    def current(self, sid: str) -> InstanceView:
        return self.instances.current(sid)
    def update(self, sid: str, update: ContextUpdate, expected_revision: int | None = None, *, actor="editor") -> UpdateResult:
        return self.mutations.apply(sid, update, expected_revision, actor=actor)
    def template_instances(self, tid: str) -> list[InstanceInfo]:
        return self.instances.list(str(tid))
    def named_instance(self, tid: str, slug: str) -> InstanceView:
        return self.instances.named(tid, slug)
    def connect_instance(self, tid: str, slug: str, *, name: str | None = None, agent_id: str | None = None,
                         create: bool = True) -> ConnectionResult:
        return self.instances.connect(tid, slug, name=name, agent_id=agent_id, create=create)
    def assign_instance(self, sid: str, tid: str, slug: str, *, agent_id: str | None = None) -> InstanceView:
        return self.instances.assign(sid, tid, slug, agent_id=agent_id)
    def rename_instance(self, sid: str, name: str, slug: str, agent_id: str | None, expected_revision: int) -> InstanceView:
        return self.instances.rename(sid, name, slug, agent_id, expected_revision)
    def templates(self) -> list[TemplateInfo]:
        return self.template_service.list()
    def template(self, tid: str) -> TemplateView:
        return self.template_service.get(tid)
    def create_template(self, name: str, description: str = "", *, source_template_id: str | None = None,
                        source_context_id: str | None = None) -> TemplateView:
        return self.template_service.create(name, description, source_template_id=source_template_id, source_context_id=source_context_id)
    def update_template(self, tid: str, update: ContextUpdate, expected_revision: int | None = None) -> TemplateView:
        return self.template_service.update(tid, update, expected_revision)
    def rename_template(self, tid: str, name: str, description: str, expected_revision: int) -> TemplateView:
        return self.template_service.rename(tid, name, description, expected_revision)
    def build(self, sid: str, budget_chars: int = 24000) -> CompiledContext:
        return self.compilation.instance(sid, budget_chars)
    def build_template(self, tid: str, budget_chars: int = 24000) -> CompiledContext:
        return self.compilation.template(tid, budget_chars)
    def export(self, sid: str) -> InstanceExport:
        return self.compilation.export_instance(sid)
    def export_template(self, tid: str) -> TemplateExport:
        return self.compilation.export_template(tid)
