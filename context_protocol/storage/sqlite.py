from pathlib import Path
from threading import RLock
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from .sql import SQLRepository


class SQLiteRepository(SQLRepository):
    row_locks = False

    def __init__(self, url):
        memory = url.database in (None, "", ":memory:")
        if not memory:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        options = {"poolclass": StaticPool} if memory else {}
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, **options)

        @event.listens_for(engine, "connect")
        def setup(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            dbapi_connection.execute("PRAGMA busy_timeout=30000")

        @event.listens_for(engine, "begin")
        def begin(connection):
            # Acquire writer lock before reading the version, including across processes.
            connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.get_execution_options().get("context_write") else "BEGIN")

        super().__init__(engine, "sqlite")
        if memory:
            self.transaction_lock = RLock()
