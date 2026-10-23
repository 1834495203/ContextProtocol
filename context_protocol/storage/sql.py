"""SQL connection and transaction lifecycle. Mapping belongs to mappers/."""
from contextlib import contextmanager, nullcontext
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from .base import StorageError
from ..models.persistence import StorageLocation
from ..mappers.schema import make_tables
from ..mappers.sql_mapper import SqlMapper

# Existing integrations importing this symbol keep their transaction hooks.
SQLUnitOfWork = SqlMapper


class SQLRepository:
    row_locks = True

    def __init__(self, engine, backend):
        self.engine, self.backend = engine, backend
        self.metadata, self.tables = make_tables()
        self.transaction_lock = None

    def initialize(self):
        try:
            self.metadata.create_all(self.engine)
            from .legacy_import import import_current_contexts
            import_current_contexts(self)
        except SQLAlchemyError as exc:
            raise StorageError("Storage initialization failed") from exc

    def describe(self):
        url = self.engine.url
        return StorageLocation(backend=self.backend, database=url.database, host=url.host, port=url.port)

    def health(self):
        with self.transaction() as tx:
            tx.connection.execute(select(self.tables["contexts"].c.session_id).limit(1))

    def close(self):
        self.engine.dispose()

    @contextmanager
    def transaction(self, *, write=False):
        try:
            with self.transaction_lock if self.transaction_lock is not None else nullcontext():
                with self.engine.connect().execution_options(context_write=write) as conn:
                    with conn.begin():
                        yield SqlMapper(conn, self.tables, self.row_locks)
        except SQLAlchemyError as exc:
            raise StorageError("Storage transaction failed") from exc
