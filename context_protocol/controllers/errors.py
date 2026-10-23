from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ..models.persistence import ErrorResponse
from ..services.errors import Conflict, NotFound
from ..storage.base import StorageError

async def conflict_handler(request: Request, exc: Conflict):
    return JSONResponse(status_code=409, content=ErrorResponse(detail=str(exc)).model_dump())

async def missing_handler(request: Request, exc: NotFound):
    return JSONResponse(status_code=404, content=ErrorResponse(detail=str(exc)).model_dump())

async def validation_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content=ErrorResponse(detail=str(exc)).model_dump())

async def storage_handler(request: Request, exc: StorageError):
    return JSONResponse(status_code=503, content=ErrorResponse(detail="存储操作失败，请检查 CONTEXT_DATABASE_URL 和存储服务。").model_dump())

def register_errors(app: FastAPI) -> None:
    app.add_exception_handler(Conflict, conflict_handler)
    app.add_exception_handler(NotFound, missing_handler)
    app.add_exception_handler(ValueError, validation_handler)
    app.add_exception_handler(StorageError, storage_handler)
