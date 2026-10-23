"""Database row models and service results, shared by mapper interfaces."""
from datetime import datetime
from typing import Literal
from pydantic import Field
from .context import Model, ContextState


class SessionRow(Model):
    id: str
    name: str
    version: int
    created_at: datetime


class InstanceInfo(Model):
    id: str
    name: str
    revision: int
    created_at: datetime
    slug: str | None = None
    template_id: str | None = None
    template_revision: int | None = None
    agent_id: str | None = None
    origin: Literal["created", "assigned"] | None = None


class InstanceListRow(SessionRow):
    template_id: str | None = None
    template_revision: int | None = None
    agent_id: str | None = None
    slug: str | None = None
    origin: Literal["created", "assigned"] | None = None


class ContextRow(Model):
    session_id: str
    state: ContextState
    updated_at: datetime


class ReceiptRow(Model):
    session_id: str
    update_id: str
    digest: str
    revision: int


class TemplateInfo(Model):
    id: str
    name: str
    description: str
    revision: int
    created_at: datetime
    updated_at: datetime


class TemplateRow(TemplateInfo):
    state: ContextState


class BindingRow(Model):
    session_id: str
    template_id: str
    template_revision: int
    agent_id: str | None = None


class HandleRow(Model):
    session_id: str
    template_id: str
    slug: str
    origin: Literal["created", "assigned"]


class InstanceBinding(BindingRow):
    slug: str | None = None
    origin: Literal["created", "assigned"] | None = None


class InstanceView(Model):
    session: InstanceInfo
    revision: int
    updated_at: datetime
    state: ContextState
    binding: InstanceBinding | None = None


class TemplateView(Model):
    template: TemplateInfo
    revision: int
    state: ContextState


class UpdateReceipt(Model):
    update_id: str
    seq: int


class UpdateResult(Model):
    update: UpdateReceipt
    duplicate: bool


class ConnectionResult(Model):
    instance: InstanceInfo
    created: bool


class StorageLocation(Model):
    backend: str
    database: str | None = None
    host: str | None = None
    port: int | None = None


class ErrorResponse(Model):
    detail: str


class HealthResponse(Model):
    status: str = "ok"
    database: str
    storage: StorageLocation
    protocol_version: str = "0.1"
