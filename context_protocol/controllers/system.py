from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from ..models.agent_guide import AgentGuide, GuideLinks
from ..services.agent_guide import build_agent_guide, render_agent_guide
from ..models.context import Model, JsonObject, Group, ToolDefinition, RegisteredTool, DEFAULT_GROUPS, STATE_TOOL
from ..models.context_tools import context_tool_catalog, ContextToolCatalog
from ..models.commands import ContextUpdate
from ..models.host import HOST_PROTOCOL_VERSION, PrepareInput, PreparedContext
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
    context_tools: list[RegisteredTool]
    context_tools_auto_register: bool = False
    host_protocol_versions: list[str]
    host_profile: str = "text-tools"
    prepare_schema: JsonObject
    prepared_schema: JsonObject
    tool_binding_commands: list[str] = ["host.open", "host.renew", "host.close", "provider.sync"]
    tool_prompt_commands: list[str] = ["tool.prompt.set", "tool.prompt.delete"]
    preferred_tool_provider: str = "mcp"

router = APIRouter(tags=["system"])


@router.get('/api/context-tools', summary='可选上下文工具定义，发现不会自动注册执行器')
def list_context_tools() -> ContextToolCatalog:
    return ContextToolCatalog(tools=context_tool_catalog())


@router.get('/api/agent-guide', response_model=AgentGuide,
            summary='开发 AI 的完整 Agent 接入说明（只读，无需数据库）',
            responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}})
def agent_guide(request: Request, format: Literal['json', 'markdown'] = Query(default='json')):
    base = str(request.base_url).rstrip('/')
    links = GuideLinks(guide=str(request.url_for('agent_guide')),
        markdown=str(request.url_for('agent_guide'))+'?format=markdown',
        openapi=str(request.url_for('openapi')), protocol=str(request.url_for('protocol')),
        health=str(request.url_for('health')), api_docs=str(request.url_for('swagger_ui_html')),
        context_tools=str(request.url_for('list_context_tools')))
    guide = build_agent_guide(base, links)
    if format == 'markdown':
        return PlainTextResponse(render_agent_guide(guide), media_type='text/markdown')
    return guide

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
        sources=sorted(store.compilation.compiler.sources.providers), state_tool=STATE_TOOL,
        context_tools=context_tool_catalog(),
        host_protocol_versions=[HOST_PROTOCOL_VERSION],
        prepare_schema=JsonObject(PrepareInput.model_json_schema()),
        prepared_schema=JsonObject(PreparedContext.model_json_schema()))
