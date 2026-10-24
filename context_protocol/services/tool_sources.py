"""Persistent central tool definitions, independent of their live connections."""
from __future__ import annotations
from datetime import datetime, timezone
from ..models.gateway import ToolSourceInput, ToolSourceRow, ToolSourceView
from ..models.context import RegisteredTool
from ..storage.base import ContextRepository
from .errors import NotFound, Conflict


def source_view(row: ToolSourceRow) -> ToolSourceView:
    return ToolSourceView.model_validate(row.model_dump())


class ToolSourceService:
    def __init__(self, repository: ContextRepository):
        self.repository = repository

    def list(self) -> list[ToolSourceView]:
        with self.repository.transaction() as tx:
            return [source_view(row) for row in tx.tool_sources()]

    def get(self, source_id: str) -> ToolSourceRow:
        with self.repository.transaction() as tx:
            row = tx.tool_source(source_id)
            if row is None:
                raise NotFound('Tool source does not exist')
            return row

    def save(self, request: ToolSourceInput) -> ToolSourceView:
        with self.repository.transaction(write=True) as tx:
            previous = tx.tool_source(request.id, lock=True)
            if previous is not None and request.expected_revision != previous.revision:
                raise Conflict('Tool source changed; supply its current expected_revision')
            if previous is None and request.expected_revision not in (None, 0):
                raise Conflict('Tool source no longer exists')
            row = ToolSourceRow(id=request.id, title=request.title, connection=request.connection,
                enabled=request.enabled, revision=previous.revision+1 if previous else 1,
                tools=previous.tools if previous else [])
            tx.save_tool_source(row)
            return source_view(row)

    def discovered(self, source_id: str, revision: int, tools: list[RegisteredTool], error: str | None = None) -> ToolSourceView:
        with self.repository.transaction(write=True) as tx:
            row = tx.tool_source(source_id, lock=True)
            if row is None or row.revision != revision:
                raise Conflict('Tool source changed during discovery')
            if error is None:
                row.tools, row.discovered_at = tools, datetime.now(timezone.utc)
            row.last_error = error
            tx.save_tool_source(row)
            return source_view(row)

    def delete(self, source_id: str):
        with self.repository.transaction(write=True) as tx:
            if tx.tool_source(source_id, lock=True) is None:
                raise NotFound('Tool source does not exist')
            tx.delete_tool_source(source_id)
