"""Tool identities, annotation overlays and per-host resolution."""
from datetime import datetime, timezone
import hashlib
import json
from ..models.context import ContextState, ToolDefinition, JsonObject
from ..models.host import PreparationIssue


def contract_digest(schema: JsonObject) -> str:
    # Only remove schema annotations, never properties with those names.
    def strip(node):
        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                if key in ("description", "title", "examples", "$comment"):
                    continue
                if key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas") and isinstance(value, dict):
                    result[key] = {name: strip(child) for name, child in value.items()}
                elif key in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames",
                             "unevaluatedProperties", "allOf", "anyOf", "oneOf", "prefixItems"):
                    result[key] = strip(value)
                else:
                    result[key] = value
            return result
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node
    return hashlib.sha256(json.dumps(strip(schema.root), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def schema_node(schema: dict, pointer: str) -> dict:
    """Resolve JSON Pointer only through JSON Schema applicators."""
    if not pointer.startswith('/'):
        raise ValueError("Parameter paths must be JSON Pointers to schema nodes")
    parts = [part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/')]
    node = schema
    index = 0
    maps = {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'}
    arrays = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    singles = {'items', 'additionalProperties', 'contains', 'not', 'if', 'then', 'else', 'propertyNames', 'unevaluatedProperties'}
    while index < len(parts):
        key = parts[index]
        if key in maps | arrays and index + 1 < len(parts):
            collection = node[key]
            child = parts[index + 1]
            node = collection[int(child)] if key in arrays else collection[child]
            index += 2
        elif key in singles:
            node = node[key]
            index += 1
        else:
            raise ValueError("Path must identify a schema node, not a value or constraint")
        if not isinstance(node, dict):
            raise ValueError("Path does not identify an object schema")
    return node


def resolve_tools(state: ContextState, binding_id: str | None = None) -> tuple[ContextState, list[PreparationIssue], int | None]:
    resolved = state.model_copy(deep=True)
    issues: list[PreparationIssue] = []
    revision = None
    if binding_id is None and state.tool_sources is not None:
        resolved.tools = {}
    if binding_id is not None:
        resolved.tools = {}
        binding = state.host_bindings.get(binding_id)
        if binding is None or not binding.active or binding.expires_at <= datetime.now(timezone.utc):
            issues.append(PreparationIssue(code="binding_unavailable", message="Host binding is missing, closed or expired; reconnect and rediscover tools."))
            return resolved, issues, revision
        revision = binding.revision
        if binding.managed_by == 'gateway' and state.tool_sources is None:
            issues.append(PreparationIssue(code='binding_unavailable', message='Central tool sources are no longer selected.'))
            return resolved, issues, revision
        for provider_id, provider in binding.providers.items():
            if binding.managed_by == 'gateway' and provider_id not in (state.tool_sources or []):
                continue
            if provider.status != 'ready':
                issues.append(PreparationIssue(code="provider_unavailable", message=f"Tool discovery is {provider.status}: {provider_id}"))
                continue
            for tool in provider.tools:
                name = tool.model_name or tool.name
                ref = provider_id + '/' + tool.name
                if name in resolved.tools:
                    issues.append(PreparationIssue(code="ambiguous_tool", message=f"Duplicate model tool name: {name}; register distinct model_name values."))
                    continue
                resolved.tools[name] = ToolDefinition(name=name, description=tool.description,
                    input_schema=tool.input_schema.model_copy(deep=True), tool_ref=ref)
    for name, tool in list(resolved.tools.items()):
        ref = tool.tool_ref or 'legacy/' + name
        prompt = state.tool_prompts.get(ref)
        if prompt is None:
            continue
        if not prompt.enabled:
            resolved.tools.pop(name)
            continue
        try:
            if prompt.contract_sha256 and prompt.contract_sha256 != contract_digest(tool.input_schema):
                raise ValueError("tool contract changed")
            schema = tool.input_schema.model_copy(deep=True)
            for pointer, description in prompt.parameter_descriptions.items():
                schema_node(schema.root, pointer)['description'] = description
            tool.input_schema = schema
            if prompt.description is not None:
                tool.description = prompt.description
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            resolved.tools.pop(name)
            issues.append(PreparationIssue(code="incompatible_tool", message=f"Review tool prompt for {ref}: {exc}"))
    present = {tool.tool_ref or 'legacy/' + name for name, tool in resolved.tools.items()}
    for ref, prompt in state.tool_prompts.items():
        if prompt.required and prompt.enabled and ref not in present:
            issues.append(PreparationIssue(code="missing_required_tool", message=f"Required tool is unavailable: {ref}"))
    return resolved, issues, revision
