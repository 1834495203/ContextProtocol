"""Instance HTTP routes: validation/delegation/response only."""
from uuid import UUID
from fastapi import APIRouter, Request, Response, Query
from ..models.requests import SessionInput, BuildInput, InstanceInput, ConnectInput, AssignInput, InstanceMetadata
from ..models.commands import ContextUpdate
from ..models.persistence import InstanceInfo, InstanceView, UpdateResult, ConnectionResult
from ..models.compilation import CompiledContext
from ..models.host import PrepareInput, PreparedContext
from ..services.compilation import InstanceExport
from .dependencies import ApplicationDep, current_only, optional_id

router = APIRouter(tags=["instances"])

@router.get("/api/sessions")
def list_sessions(store: ApplicationDep) -> list[InstanceInfo]:
    return store.sessions()


@router.post("/api/sessions", status_code=201)
def create_session(body: SessionInput, store: ApplicationDep) -> InstanceInfo:
    if body.demo:
        return store.create_demo_session(body.name)
    return store.create_session(body.name, template_id=optional_id(body.template_id), agent_id=body.agent_id, slug=body.slug)


@router.get("/api/sessions/{sid}")
def get_session(sid: UUID, request: Request, store: ApplicationDep) -> InstanceView:
    current_only(request)
    return store.current(str(sid))


@router.post("/api/sessions/{sid}/updates", status_code=201)
def update_context(sid: UUID, body: ContextUpdate, store: ApplicationDep) -> UpdateResult:
    return store.update(str(sid), body, actor="editor")


@router.post("/api/sessions/{sid}/ingest", status_code=201)
def ingest_context(sid: UUID, body: ContextUpdate, store: ApplicationDep) -> UpdateResult:
    return store.update(str(sid), body, actor="producer")


@router.post("/api/sessions/{sid}/compile")
def build(sid: UUID, body: BuildInput, store: ApplicationDep) -> CompiledContext:
    return store.build(str(sid), body.budget_chars)


@router.post("/api/sessions/{sid}/prepare")
def prepare(sid: UUID, body: PrepareInput, store: ApplicationDep) -> PreparedContext:
    return store.compilation.prepare(str(sid), body)


@router.get("/api/sessions/{sid}/preview")
def preview(sid: UUID, request: Request, store: ApplicationDep, budget_chars: int = Query(24000, ge=100, le=2000000),
            binding_id: str | None = None) -> CompiledContext:
    current_only(request)
    return store.compilation.instance(str(sid), budget_chars, binding_id)


@router.get("/api/sessions/{sid}/export")
def export(sid: UUID, request: Request, response: Response, store: ApplicationDep, binding_id: str | None = None) -> InstanceExport:
    current_only(request)
    response.headers["Content-Disposition"] = f'attachment; filename="context-{sid}-current.json"'
    return store.compilation.export_instance(str(sid), binding_id)


@router.get("/api/instances/unassigned")
def unassigned_instances(store: ApplicationDep) -> list[InstanceInfo]:
    return store.unassigned_instances()


@router.get("/api/templates/{tid}/instances")
def template_instances(tid: UUID, store: ApplicationDep) -> list[InstanceInfo]:
    return store.template_instances(str(tid))


@router.post("/api/templates/{tid}/instances", status_code=201)
def create_instance(tid: UUID, body: InstanceInput, store: ApplicationDep) -> InstanceInfo:
    return store.create_session(body.name, template_id=str(tid), agent_id=body.agent_id, slug=body.slug)


@router.post("/api/templates/{tid}/instances/connect")
def connect_instance(tid: UUID, body: ConnectInput, store: ApplicationDep) -> ConnectionResult:
    return store.connect_instance(str(tid), body.slug, name=body.name, agent_id=body.agent_id, create=body.create)


@router.get("/api/templates/{tid}/instances/{slug}")
def named_instance(tid: UUID, slug: str, store: ApplicationDep) -> InstanceView:
    return store.named_instance(str(tid), slug)


@router.post("/api/sessions/{sid}/assign")
def assign_instance(sid: UUID, body: AssignInput, store: ApplicationDep) -> InstanceView:
    return store.assign_instance(str(sid), str(body.template_id), body.slug, agent_id=body.agent_id)


@router.post("/api/sessions/{sid}/metadata")
def rename_instance(sid: UUID, body: InstanceMetadata, store: ApplicationDep) -> InstanceView:
    return store.rename_instance(str(sid), body.name, body.slug, body.agent_id, body.expected_revision)
