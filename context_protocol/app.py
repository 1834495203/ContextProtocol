"""Application composition root: configuration, lifespan and controller registration."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .controllers import instances, templates, system, tool_sources
from .controllers.errors import register_errors
from .services.application import ContextApplication
from .settings import Settings
from .storage.base import StorageError
from .storage.factory import create_repository

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def create_app(context_store: ContextApplication | None = None, *, local_tool_providers=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if application.state.store is None:
            application.state.store = ContextApplication(create_repository(Settings.from_env().database_url))
        try:
            application.state.store.initialize()
        except StorageError:
            logging.getLogger(__name__).warning("Storage unavailable; check CONTEXT_DATABASE_URL.")
        from .runtime.gateway import ToolGateway
        application.state.gateway = ToolGateway(application.state.store, local_tool_providers=local_tool_providers)
        try:
            await application.state.gateway.start()
        except StorageError:
            logging.getLogger(__name__).warning('Tool gateway is waiting for storage availability.')
        try:
            yield
        finally:
            await application.state.gateway.close()
            application.state.store.close()

    application = FastAPI(title="Context Protocol", version="0.8.0", lifespan=lifespan)
    application.state.store = context_store
    application.state.gateway = None
    application.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    register_errors(application)
    for controller in (system.router, templates.router, instances.router, tool_sources.router):
        application.include_router(controller)
    return application

app = create_app()
