"""Wire contract for framework-independent host preparation."""
from typing import Literal
from pydantic import ConfigDict, Field
from .context import Model
from .compilation import CompiledContext

HOST_PROTOCOL_VERSION = "0.1"


class HostCapabilities(Model):
    model_config = ConfigDict(extra="forbid")
    roles: list[Literal["system", "user", "assistant", "tool"]] = Field(
        default_factory=lambda: ["system", "user", "assistant", "tool"])
    tool_calls: bool = True


class PrepareInput(Model):
    model_config = ConfigDict(extra="forbid")
    supported_versions: list[str] = Field(default_factory=lambda: [HOST_PROTOCOL_VERSION], min_length=1)
    capabilities: HostCapabilities = Field(default_factory=HostCapabilities)
    budget_chars: int = Field(default=24000, ge=100, le=2000000)
    binding_id: str | None = None
    expected_binding_revision: int | None = Field(default=None, ge=0)


class PreparationIssue(Model):
    code: Literal["unsupported_version", "over_budget", "truncated_tool_schema", "unsupported_role", "unsupported_tool_calls",
                  "binding_required", "binding_unavailable", "binding_changed", "provider_unavailable", "ambiguous_tool",
                  "incompatible_tool", "missing_required_tool"]
    message: str


class PreparedContext(Model):
    protocol_version: str = HOST_PROTOCOL_VERSION
    ready: bool
    issues: list[PreparationIssue] = Field(default_factory=list)
    compiled: CompiledContext | None = None
    binding_id: str | None = None
    binding_revision: int | None = None
