"""Legacy table mapping for one-time import only. New context writes never use it."""

from sqlalchemy import BigInteger, Column, ForeignKey, Index, JSON, MetaData, String, Table, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB



from ..mappers.types import UTCDateTime, OpaqueIdentifier as EventIdentifier


def make_tables():
    metadata = MetaData()
    json_type = JSON().with_variant(JSONB(), "postgresql")
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    sessions = Table("sessions", metadata,
        Column("id", String(36), primary_key=True), Column("name", Text, nullable=False),
        Column("version", BigInteger, nullable=False), Column("created_at", UTCDateTime(), nullable=False), **options)
    events = Table("events", metadata,
        Column("session_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("seq", BigInteger, primary_key=True), Column("event_id", EventIdentifier(), nullable=False),
        Column("type", String(64), nullable=False), Column("source", String(200), nullable=False),
        Column("turn_id", String(128)), Column("protocol_version", String(16), nullable=False),
        Column("payload", json_type, nullable=False), Column("created_at", UTCDateTime(), nullable=False),
        UniqueConstraint("session_id", "event_id", name="uq_event_identity"), **options)
    snapshots = Table("snapshots", metadata,
        Column("session_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("seq", BigInteger, primary_key=True), Column("state", json_type, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False), **options)
    builds = Table("builds", metadata,
        Column("id", String(36), primary_key=True), Column("session_id", String(36), ForeignKey("sessions.id"), nullable=False),
        Column("seq", BigInteger, nullable=False), Column("artifact", json_type, nullable=False),
        Column("sha256", String(64), nullable=False), Column("created_at", UTCDateTime(), nullable=False), **options)
    Index("ix_builds_session_created", builds.c.session_id, builds.c.created_at)
    branches = Table("branches", metadata,
        Column("id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("root_id", String(36), ForeignKey("sessions.id"), nullable=False),
        Column("parent_id", String(36), ForeignKey("branches.id")), Column("base_seq", BigInteger, nullable=False),
        Column("name", String(120), nullable=False), Column("created_at", UTCDateTime(), nullable=False), **options)
    selections = Table("context_selections", metadata,
        Column("root_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("branch_id", String(36), ForeignKey("branches.id"), nullable=False),
        Column("seq", BigInteger), Column("updated_at", UTCDateTime(), nullable=False), **options)
    return metadata, {"sessions": sessions, "events": events, "snapshots": snapshots, "builds": builds,
                      "branches": branches, "context_selections": selections}


class SQLUnitOfWork:
    def __init__(self, connection, tables, row_locks):
        self.connection, self.tables, self.row_locks = connection, tables, row_locks

    def _one(self, statement):
        row = self.connection.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def _all(self, statement):
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def sessions(self):
        t = self.tables["sessions"]
        return self._all(select(t).order_by(t.c.created_at.desc(), t.c.id))

    def session(self, sid, lock=False):
        t = self.tables["sessions"]
        query = select(t).where(t.c.id == sid)
        return self._one(query.with_for_update() if lock and self.row_locks else query)

    def event(self, sid, event_id):
        t = self.tables["events"]
        return self._one(select(t).where(t.c.session_id == sid, t.c.event_id == event_id))

    def events(self, sid, after=0, upto=None, limit=None):
        t = self.tables["events"]
        query = select(t).where(t.c.session_id == sid, t.c.seq > after).order_by(t.c.seq)
        if upto is not None:
            query = query.where(t.c.seq <= upto)
        return self._all(query.limit(limit) if limit is not None else query)

    def latest_snapshot(self, sid, seq):
        t = self.tables["snapshots"]
        return self._one(select(t).where(t.c.session_id == sid, t.c.seq <= seq).order_by(t.c.seq.desc()).limit(1))

    def snapshots(self, sid, limit=None):
        t = self.tables["snapshots"]
        query = select(t).where(t.c.session_id == sid).order_by(t.c.seq.desc())
        return self._all(query.limit(limit) if limit is not None else query)

    def builds(self, sid, upto=None, limit=None):
        t = self.tables["builds"]
        query = select(t).where(t.c.session_id == sid).order_by(t.c.created_at.desc(), t.c.id)
        if upto is not None:
            query = query.where(t.c.seq <= upto)
        return self._all(query.limit(limit) if limit is not None else query)

    def build(self, sid, bid):
        t = self.tables["builds"]
        return self._one(select(t).where(t.c.session_id == sid, t.c.id == bid))

    def branch(self, bid):
        t = self.tables["branches"]
        return self._one(select(t).where(t.c.id == bid))

    def branches(self, root_id):
        t = self.tables["branches"]
        return self._all(select(t).where(t.c.root_id == root_id).order_by(t.c.created_at, t.c.id))

    def selection(self, root_id):
        t = self.tables["context_selections"]
        return self._one(select(t).where(t.c.root_id == root_id))
