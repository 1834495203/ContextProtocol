from ..models.context import Model, ContextState
from ..models.persistence import InstanceInfo, InstanceBinding, TemplateInfo
from ..models.compilation import CompiledContext
from ..domain.compiler import ContextCompiler
from ..domain.preparation import assess_input
from ..domain.tools import resolve_tools
from ..models.host import HOST_PROTOCOL_VERSION, PrepareInput, PreparedContext, PreparationIssue
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

    def prepare(self, sid: str, request: PrepareInput) -> PreparedContext:
        if HOST_PROTOCOL_VERSION not in request.supported_versions:
            return PreparedContext(ready=False, issues=[PreparationIssue(
                code="unsupported_version", message=f"Supported host protocol versions: {HOST_PROTOCOL_VERSION}")])
        current = self.instances.current(sid)
        state, issues, revision = resolve_tools(current.state, request.binding_id)
        if request.binding_id is None and current.state.host_bindings:
            issues.append(PreparationIssue(code="binding_required", message="Select a host binding before preparing this instance."))
        if request.expected_binding_revision is not None and revision != request.expected_binding_revision:
            issues.append(PreparationIssue(code="binding_changed", message="Tool binding changed; rediscover before calling tools."))
        compiled = self.compiler.compile(state, request.budget_chars)
        offered = {t.tool_ref or 'legacy/' + t.name for t in compiled.model_input.tools}
        for ref, prompt in state.tool_prompts.items():
            if prompt.required and prompt.enabled and ref not in offered and not any(
                issue.code == 'missing_required_tool' and ref in issue.message for issue in issues):
                issues.append(PreparationIssue(code="missing_required_tool", message=f"Required tool is excluded from model input: {ref}"))
        compiled.session_id, compiled.revision = current.session.id, current.revision
        result = assess_input(compiled, request.capabilities)
        result.issues.extend(issues)
        result.ready = not result.issues
        result.binding_id, result.binding_revision = request.binding_id, revision
        return result

    def instance(self, sid: str, budget_chars: int = 24000, binding_id: str | None = None) -> CompiledContext:
        current = self.instances.current(sid)
        state, issues, _ = resolve_tools(current.state, binding_id)
        result = self.compiler.compile(state, budget_chars)
        result.warnings.extend(issue.message for issue in issues)
        result.session_id, result.revision = current.session.id, current.revision
        return result

    def template(self, tid: str, budget_chars: int = 24000) -> CompiledContext:
        current = self.templates.get(tid)
        state, issues, _ = resolve_tools(current.state)
        result = self.compiler.compile(state, budget_chars)
        result.warnings.extend(issue.message for issue in issues)
        result.template_id, result.revision = current.template.id, current.revision
        return result

    def export_instance(self, sid: str, binding_id: str | None = None) -> InstanceExport:
        current = self.instances.current(sid)
        state, issues, _ = resolve_tools(current.state, binding_id)
        compiled = self.compiler.compile(state)
        compiled.warnings.extend(issue.message for issue in issues)
        return InstanceExport(session=current.session, revision=current.revision, updated_at=current.updated_at,
            state=current.state, binding=current.binding, compiled=compiled)

    def export_template(self, tid: str) -> TemplateExport:
        current = self.templates.get(tid)
        state, issues, _ = resolve_tools(current.state)
        compiled = self.compiler.compile(state)
        compiled.warnings.extend(issue.message for issue in issues)
        return TemplateExport(template=current.template, revision=current.revision, state=current.state,
                              compiled=compiled)
