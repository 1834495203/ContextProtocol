"""The only live boundary translating SQL rows/JSON into typed business models."""
from typing import TypeVar
from pydantic import BaseModel
from sqlalchemy import select, Table
from sqlalchemy.engine import Connection
from sqlalchemy.sql import Executable
from ..models.persistence import (SessionRow, InstanceListRow, ContextRow, ReceiptRow, TemplateInfo,
                                  TemplateRow, BindingRow, HandleRow)

RowType = TypeVar("RowType", bound=BaseModel)


class SqlMapper:
    def __init__(self, connection: Connection, tables: dict[str, Table], row_locks: bool):
        self.connection, self.tables, self.row_locks = connection, tables, row_locks

    def _one(self, statement: Executable, model: type[RowType]) -> RowType | None:
        row = self.connection.execute(statement).mappings().first()
        return model.model_validate(dict(row)) if row is not None else None

    def sessions(self, template_id: str | None = None, unassigned: bool = False) -> list[InstanceListRow]:
        s, c, b, h = [self.tables[n] for n in ("sessions", "contexts", "context_bindings", "context_handles")]
        query = select(s, b.c.template_id, b.c.template_revision, b.c.agent_id, h.c.slug, h.c.origin).join(
            c, c.c.session_id == s.c.id).outerjoin(b, b.c.session_id == s.c.id).outerjoin(h, h.c.session_id == s.c.id)
        if template_id:
            query = query.where(b.c.template_id == template_id)
        if unassigned:
            query = query.where(b.c.session_id.is_(None))
        return [InstanceListRow.model_validate(dict(r)) for r in self.connection.execute(query.order_by(s.c.created_at.desc(), s.c.id)).mappings()]

    def session(self, sid: str, lock: bool = False) -> SessionRow | None:
        t = self.tables["sessions"]
        query = select(t).where(t.c.id == sid)
        return self._one(query.with_for_update() if lock and self.row_locks else query, SessionRow)

    def insert_session(self, row: SessionRow) -> None:
        self.connection.execute(self.tables["sessions"].insert().values(**row.model_dump(mode="python")))

    def set_version(self, sid: str, version: int) -> None:
        t = self.tables["sessions"]
        self.connection.execute(t.update().where(t.c.id == sid).values(version=version))

    def context(self, sid: str) -> ContextRow | None:
        t = self.tables["contexts"]
        return self._one(select(t).where(t.c.session_id == sid), ContextRow)

    def save_context(self, row: ContextRow) -> None:
        t = self.tables["contexts"]
        values = row.model_dump(mode="python", exclude={"state"})
        values["state"] = row.state.model_dump(mode="json")
        if self.context(row.session_id) is None:
            self.connection.execute(t.insert().values(**values))
        else:
            self.connection.execute(t.update().where(t.c.session_id == row.session_id).values(**values))

    def receipt(self, sid: str, update_id: str) -> ReceiptRow | None:
        t = self.tables["context_receipts"]
        return self._one(select(t).where(t.c.session_id == sid, t.c.update_id == update_id), ReceiptRow)

    def insert_receipt(self, row: ReceiptRow) -> None:
        self.connection.execute(self.tables["context_receipts"].insert().values(**row.model_dump(mode="python")))

    def templates(self) -> list[TemplateInfo]:
        t = self.tables["context_templates"]
        return [TemplateInfo.model_validate(dict(r)) for r in self.connection.execute(select(t.c.id, t.c.name, t.c.description,
                t.c.revision, t.c.created_at, t.c.updated_at).order_by(t.c.created_at, t.c.id)).mappings()]

    def template(self, tid: str, lock: bool = False) -> TemplateRow | None:
        t = self.tables["context_templates"]
        query = select(t).where(t.c.id == tid)
        return self._one(query.with_for_update() if lock and self.row_locks else query, TemplateRow)

    def insert_template(self, row: TemplateRow) -> None:
        values = row.model_dump(mode="python", exclude={"state"})
        values["state"] = row.state.model_dump(mode="json")
        self.connection.execute(self.tables["context_templates"].insert().values(**values))

    def save_template(self, row: TemplateRow) -> None:
        t = self.tables["context_templates"]
        values = row.model_dump(mode="python", exclude={"state"})
        values["state"] = row.state.model_dump(mode="json")
        self.connection.execute(t.update().where(t.c.id == row.id).values(**values))

    def binding(self, sid: str) -> BindingRow | None:
        t = self.tables["context_bindings"]
        return self._one(select(t).where(t.c.session_id == sid), BindingRow)

    def insert_binding(self, row: BindingRow) -> None:
        self.connection.execute(self.tables["context_bindings"].insert().values(**row.model_dump(mode="python")))

    def save_binding(self, row: BindingRow) -> None:
        t = self.tables["context_bindings"]
        self.connection.execute(t.update().where(t.c.session_id == row.session_id).values(**row.model_dump(mode="python")))

    def handle(self, sid: str) -> HandleRow | None:
        t = self.tables["context_handles"]
        return self._one(select(t).where(t.c.session_id == sid), HandleRow)

    def named_instance(self, tid: str, slug: str) -> HandleRow | None:
        t = self.tables["context_handles"]
        return self._one(select(t).where(t.c.template_id == tid, t.c.slug == slug), HandleRow)

    def insert_handle(self, row: HandleRow) -> None:
        self.connection.execute(self.tables["context_handles"].insert().values(**row.model_dump(mode="python")))

    def save_instance_metadata(self, sid: str, name: str, slug: str, agent_id: str | None) -> None:
        s, h, b = [self.tables[n] for n in ("sessions", "context_handles", "context_bindings")]
        self.connection.execute(s.update().where(s.c.id == sid).values(name=name))
        self.connection.execute(h.update().where(h.c.session_id == sid).values(slug=slug))
        self.connection.execute(b.update().where(b.c.session_id == sid).values(agent_id=agent_id))
