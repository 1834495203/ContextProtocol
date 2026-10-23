from ..models.context import Model, ContextState
from ..models.persistence import InstanceInfo, InstanceBinding, TemplateInfo
from ..models.compilation import CompiledContext
from ..domain.compiler import ContextCompiler
from .instances import InstanceService
from .templates import TemplateService
from datetime import datetime

class InstanceExport(Model):
    format: str = "context-current/0.3"
    session: InstanceInfo
    revision: int
    updated_at: datetime
    state: ContextState
    binding: InstanceBinding | None
    compiled: CompiledContext

class TemplateExport(Model):
    format: str = "context-template/0.4"
    template: TemplateInfo
    revision: int
    state: ContextState
    compiled: CompiledContext

class CompilationService:
    def __init__(self, instances: InstanceService, templates: TemplateService, compiler: ContextCompiler):
        self.instances, self.templates, self.compiler = instances, templates, compiler

    def instance(self, sid: str, budget_chars: int = 24000) -> CompiledContext:
        current = self.instances.current(sid)
        result = self.compiler.compile(current.state, budget_chars)
        result.session_id, result.revision = current.session.id, current.revision
        return result

    def template(self, tid: str, budget_chars: int = 24000) -> CompiledContext:
        current = self.templates.get(tid)
        result = self.compiler.compile(current.state, budget_chars)
        result.template_id, result.revision = current.template.id, current.revision
        return result

    def export_instance(self, sid: str) -> InstanceExport:
        current = self.instances.current(sid)
        return InstanceExport(session=current.session, revision=current.revision, updated_at=current.updated_at,
            state=current.state, binding=current.binding, compiled=self.compiler.compile(current.state))

    def export_template(self, tid: str) -> TemplateExport:
        current = self.templates.get(tid)
        return TemplateExport(template=current.template, revision=current.revision, state=current.state,
                              compiled=self.compiler.compile(current.state))
