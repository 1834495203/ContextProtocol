"""Portable SQL value types, independent of application and archive readers."""
from datetime import timezone
from sqlalchemy import String
from sqlalchemy.dialects.mysql import DATETIME, VARBINARY
from sqlalchemy.types import DateTime, TypeDecorator

class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DATETIME(fsp=6) if dialect.name == "mysql" else DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = value.astimezone(timezone.utc)
            if dialect.name != "postgresql":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value


class OpaqueIdentifier(TypeDecorator):
    """Keep IDs case-sensitive, including trailing spaces, on every backend."""
    impl = String(128)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(VARBINARY(512) if dialect.name == "mysql" else String(128))

    def process_bind_param(self, value, dialect):
        return value.encode("utf-8") if value is not None and dialect.name == "mysql" else value

    def process_result_value(self, value, dialect):
        return value.decode("utf-8") if isinstance(value, bytes) else value
