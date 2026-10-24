"""Shared readiness rules. No framework, transport or persistence dependencies."""
from ..models.compilation import CompiledContext
from ..models.host import HostCapabilities, PreparationIssue, PreparedContext


def assess_input(compiled: CompiledContext, capabilities: HostCapabilities) -> PreparedContext:
    issues: list[PreparationIssue] = []
    if compiled.over_budget:
        issues.append(PreparationIssue(code="over_budget", message="Context exceeds the character budget; adjust component policies."))
    if any(section.truncated and "tools" in section.sources for section in compiled.envelope.sections):
        issues.append(PreparationIssue(code="truncated_tool_schema", message="Native tool schemas cannot be truncated; choose keep/recent/exclude."))
    for role in sorted({m.role for m in compiled.model_input.messages} - set(capabilities.roles)):
        issues.append(PreparationIssue(code="unsupported_role", message=f"Host does not support message role: {role}"))
    if not capabilities.tool_calls and (compiled.model_input.tools or any(m.tool_calls for m in compiled.model_input.messages)):
        issues.append(PreparationIssue(code="unsupported_tool_calls", message="Host does not support native tool calls; disable tool components or use a capable host."))
    return PreparedContext(ready=not issues, issues=issues, compiled=compiled)
