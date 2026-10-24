"""Portable persistence for current contexts and idempotency receipts."""

from sqlalchemy import BigInteger, Column, ForeignKey, JSON, MetaData, String, Table, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB

from .types import UTCDateTime, OpaqueIdentifier


def make_tables():
    metadata = MetaData()
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    sessions = Table("sessions", metadata,
        Column("id", String(36), primary_key=True), Column("name", Text, nullable=False),
        Column("version", BigInteger, nullable=False), Column("created_at", UTCDateTime(), nullable=False), **options)
    contexts = Table("contexts", metadata,
        Column("session_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("state", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False), **options)
    receipts = Table("context_receipts", metadata,
        Column("session_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("update_id", OpaqueIdentifier(), primary_key=True), Column("digest", String(64), nullable=False),
        Column("revision", BigInteger, nullable=False), **options)
    templates = Table("context_templates", metadata,
        Column("id", String(36), primary_key=True), Column("name", String(120), nullable=False),
        Column("description", Text, nullable=False), Column("revision", BigInteger, nullable=False),
        Column("state", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        Column("created_at", UTCDateTime(), nullable=False), Column("updated_at", UTCDateTime(), nullable=False), **options)
    bindings = Table("context_bindings", metadata,
        Column("session_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("template_id", String(36), ForeignKey("context_templates.id"), nullable=False),
        Column("template_revision", BigInteger, nullable=False), Column("agent_id", String(200)), **options)
    handles = Table("context_handles", metadata,
        Column("session_id", String(36), ForeignKey("sessions.id"), primary_key=True),
        Column("template_id", String(36), ForeignKey("context_templates.id"), nullable=False),
        Column("slug", String(64), nullable=False), Column("origin", String(16), nullable=False),
        UniqueConstraint("template_id", "slug", name="uq_context_template_slug"), **options)
    tool_sources = Table('tool_sources', metadata,
        Column('id', String(64), primary_key=True),
        Column('document', JSON().with_variant(JSONB(), 'postgresql'), nullable=False), **options)
    return metadata, {"sessions": sessions, "contexts": contexts, "context_receipts": receipts,
                      "context_templates": templates, "context_bindings": bindings, "context_handles": handles,
                      'tool_sources': tool_sources}
