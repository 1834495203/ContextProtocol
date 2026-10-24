from datetime import datetime, timezone, timedelta
from ...models.context import HostBinding, ProviderCatalog, ToolPrompt
from ..tools import contract_digest, schema_node
from ...models.commands import (HostOpenCommand, HostCloseCommand, HostRenewCommand, ProviderSyncCommand,
                                ToolPromptSetCommand, ToolPromptDeleteCommand, ToolSourcesSetCommand)
from ...services.errors import Conflict
from .base import UpdateContext, UpdateHandler


def binding_for(context, binding_id):
    context.require_producer()
    binding = context.state.host_bindings.get(binding_id)
    if binding is None or not binding.active or binding.expires_at <= datetime.now(timezone.utc):
        raise ValueError("Host binding is missing, closed or expired; open a new binding")
    return binding


class OpenHostHandler(UpdateHandler[HostOpenCommand]):
    command_type = HostOpenCommand
    def handle(self, context, command):
        context.require_producer()
        p = command.payload
        if p.binding_id in context.state.host_bindings:
            raise ValueError("Binding ID already exists; use a new connection ID")
        context.state.host_bindings[p.binding_id] = HostBinding(id=p.binding_id, host_id=p.host_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=p.lease_seconds), lease_seconds=p.lease_seconds,
            managed_by=p.managed_by)


class CloseHostHandler(UpdateHandler[HostCloseCommand]):
    command_type = HostCloseCommand
    def handle(self, context, command):
        context.require_producer()
        binding = context.state.host_bindings.get(command.payload.binding_id)
        if binding is None:
            raise ValueError("Unknown binding")
        binding.active = False
        binding.revision += 1


class RenewHostHandler(UpdateHandler[HostRenewCommand]):
    command_type = HostRenewCommand
    def handle(self, context, command):
        binding = binding_for(context, command.payload.binding_id)
        binding.expires_at = datetime.now(timezone.utc) + timedelta(seconds=binding.lease_seconds)


class SyncProviderHandler(UpdateHandler[ProviderSyncCommand]):
    command_type = ProviderSyncCommand
    def handle(self, context, command):
        p = command.payload
        binding = binding_for(context, p.binding_id)
        if p.expected_binding_revision is not None and p.expected_binding_revision != binding.revision:
            raise Conflict("Host tool binding changed; refresh before replacing the provider catalog")
        if p.status != 'ready' and p.tools:
            raise ValueError("Only ready provider catalogs may advertise tools")
        if len({t.name for t in p.tools}) != len(p.tools):
            raise ValueError("Duplicate provider tool identity")
        names = [t.model_name or t.name for t in p.tools]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate model tool name")
        binding.providers[p.provider_id] = ProviderCatalog(kind=p.kind, status=p.status, tools=p.tools)
        if p.status == 'ready':
            for tool in p.tools:
                context.state.tool_catalog[p.provider_id + '/' + tool.name] = tool.model_copy(deep=True)
        binding.revision += 1


class SetToolPromptHandler(UpdateHandler[ToolPromptSetCommand]):
    command_type = ToolPromptSetCommand
    template_safe = True
    def handle(self, context, command):
        p = command.payload
        prompt = ToolPrompt.model_validate(p.model_dump(exclude={'base_schema'}))
        if p.base_schema is not None:
            digest = contract_digest(p.base_schema)
            if p.contract_sha256 and p.contract_sha256 != digest:
                raise ValueError("base_schema does not match the supplied contract fingerprint")
            prompt.contract_sha256 = digest
            for pointer in p.parameter_descriptions:
                try:
                    schema_node(p.base_schema.root, pointer)
                except (KeyError, ValueError, IndexError, TypeError) as exc:
                    raise ValueError(f"Invalid parameter schema path: {pointer}") from exc
        context.state.tool_prompts[p.tool_ref] = prompt


class DeleteToolPromptHandler(UpdateHandler[ToolPromptDeleteCommand]):
    command_type = ToolPromptDeleteCommand
    template_safe = True
    def handle(self, context, command):
        context.state.tool_prompts.pop(command.payload.id, None)


class SelectToolSourcesHandler(UpdateHandler[ToolSourcesSetCommand]):
    command_type = ToolSourcesSetCommand
    template_safe = True

    def handle(self, context, command):
        context.state.tool_sources = list(command.payload.sources) if command.payload.sources is not None else None
