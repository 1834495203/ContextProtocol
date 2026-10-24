"""Central source administration and agent execution routes."""
from uuid import UUID
from fastapi import APIRouter, Request, Response
from ..models.gateway import ToolSourceInput, ToolSourceView, GatewayCallInput, GatewayCallResult
from ..services.tool_sources import source_view
from .dependencies import ApplicationDep

router = APIRouter(tags=['tool-gateway'])


@router.get('/api/tool-sources')
def list_sources(store: ApplicationDep) -> list[ToolSourceView]:
    return store.tool_sources.list()


@router.post('/api/tool-sources')
async def save_source(body: ToolSourceInput, request: Request, store: ApplicationDep) -> ToolSourceView:
    result = store.tool_sources.save(body)
    await request.app.state.gateway.invalidate(body.id)
    return result


@router.get('/api/tool-sources/{source_id}')
def get_source(source_id: str, store: ApplicationDep) -> ToolSourceView:
    return source_view(store.tool_sources.get(source_id))


@router.delete('/api/tool-sources/{source_id}', status_code=204)
async def delete_source(source_id: str, request: Request, store: ApplicationDep) -> Response:
    store.tool_sources.delete(source_id)
    await request.app.state.gateway.invalidate(source_id)
    return Response(status_code=204)


@router.post('/api/tool-sources/{source_id}/discover')
async def discover_source(source_id: str, request: Request) -> ToolSourceView:
    return await request.app.state.gateway.discover(source_id)


@router.post('/api/sessions/{sid}/tools/call')
async def call_tool(sid: UUID, body: GatewayCallInput, request: Request) -> GatewayCallResult:
    return await request.app.state.gateway.call(str(sid), body)


@router.post('/api/sessions/{sid}/tools/disconnect', status_code=204)
async def disconnect_tools(sid: UUID, request: Request) -> Response:
    await request.app.state.gateway.disconnect(str(sid))
    return Response(status_code=204)
