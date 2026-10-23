"""Template HTTP routes, independent of SQL and mutation implementation."""
from uuid import UUID
from fastapi import APIRouter, Request, Response, Query
from ..models.requests import TemplateInput, TemplateMetadata
from ..models.commands import ContextUpdate
from ..models.persistence import TemplateInfo, TemplateView
from ..models.compilation import CompiledContext
from ..services.compilation import TemplateExport
from .dependencies import ApplicationDep, current_only, optional_id

router = APIRouter(tags=["templates"])

@router.get("/api/templates")
def list_templates(store: ApplicationDep) -> list[TemplateInfo]:
    return store.templates()


@router.post("/api/templates", status_code=201)
def create_template(body: TemplateInput, store: ApplicationDep) -> TemplateView:
    return store.create_template(body.name, body.description, source_template_id=optional_id(body.source_template_id),
                                 source_context_id=optional_id(body.source_context_id))


@router.get("/api/templates/{tid}")
def get_template(tid: UUID, request: Request, store: ApplicationDep) -> TemplateView:
    current_only(request)
    return store.template(str(tid))


@router.post("/api/templates/{tid}/updates")
def update_template(tid: UUID, body: ContextUpdate, store: ApplicationDep) -> TemplateView:
    return store.update_template(str(tid), body)


@router.post("/api/templates/{tid}/metadata")
def rename_template(tid: UUID, body: TemplateMetadata, store: ApplicationDep) -> TemplateView:
    return store.rename_template(str(tid), body.name, body.description, body.expected_revision)


@router.get("/api/templates/{tid}/preview")
def preview_template(tid: UUID, request: Request, store: ApplicationDep, budget_chars: int = Query(24000, ge=100, le=2000000)) -> CompiledContext:
    current_only(request)
    return store.build_template(str(tid), budget_chars)


@router.get("/api/templates/{tid}/export")
def export_template(tid: UUID, response: Response, store: ApplicationDep) -> TemplateExport:
    response.headers["Content-Disposition"] = f'attachment; filename="template-{tid}.json"'
    return store.export_template(str(tid))
