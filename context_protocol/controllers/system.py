from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from ..models.context import Model, JsonObject, Group, ToolDefinition, DEFAULT_GROUPS, STATE_TOOL
from ..models.commands import ContextUpdate
from ..models.persistence import HealthResponse, ErrorResponse
from ..storage.base import StorageError
from .dependencies import ApplicationDep

class ProtocolDescription(Model):
    protocol_version: str = "0.1"
    envelope_version: str = "0.2"
    update_schema: JsonObject
    component_schema: JsonObject
    default_components: list[Group]
    sources: list[str]
    state_tool: ToolDefinition

router = APIRouter(tags=["system"])

@router.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parents[2] / "web" / "index.html", headers={"Cache-Control":"no-cache"})

@router.get("/api/health", response_model=HealthResponse)
def health(store: ApplicationDep):
    try:
        store.health()
    except StorageError:
        return JSONResponse(status_code=503, content=ErrorResponse(detail="数据库未就绪，请配置 CONTEXT_DATABASE_URL 后重启服务。").model_dump())
    return HealthResponse(database=store.backend, storage=store.describe())

@router.get("/api/protocol")
def protocol(store: ApplicationDep) -> ProtocolDescription:
    return ProtocolDescription(update_schema=JsonObject(ContextUpdate.model_json_schema()),
        component_schema=JsonObject(Group.model_json_schema()), default_components=list(DEFAULT_GROUPS),
        sources=sorted(store.compilation.compiler.sources.providers), state_tool=STATE_TOOL)
