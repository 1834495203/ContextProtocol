"""Run the same behavior suite against each explicitly enabled backend."""
import os

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from context_protocol.settings import Settings
from context_protocol.storage.factory import create_repository
from context_protocol.store import Store

load_dotenv()
BACKENDS = ["sqlite"]
if os.getenv("CONTEXT_TEST_POSTGRES") == "1":
    BACKENDS.append("postgresql")
if os.getenv("CONTEXT_TEST_MYSQL_URL"):
    BACKENDS.append("mysql")


class TrackedStore(Store):
    """Track only sessions created by this fixture, never infer cleanup by time."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_session_ids = []
        self.test_template_ids = []

    def create_session(self, name, **kwargs):
        row = super().create_session(name, **kwargs)
        self.test_session_ids.append(row.id)
        return row

    def connect_instance(self, *args, **kwargs):
        result = super().connect_instance(*args, **kwargs)
        if result.created:
            self.test_session_ids.append(result.instance.id)
        return result

    def create_template(self, *args, **kwargs):
        row = super().create_template(*args, **kwargs)
        self.test_template_ids.append(row.template.id)
        return row


@pytest.fixture(params=BACKENDS)
def test_store(request, tmp_path):
    if request.param == "sqlite":
        url = "sqlite:///" + (tmp_path / "context.db").as_posix()
    elif request.param == "postgresql":
        url = os.getenv("CONTEXT_TEST_POSTGRES_URL") or Settings.from_env().database_url
        if not url.startswith("postgres"):
            pytest.skip("Set CONTEXT_TEST_POSTGRES_URL to a dedicated PostgreSQL test database")
    else:
        url = os.environ["CONTEXT_TEST_MYSQL_URL"]
    store = TrackedStore(create_repository(url))
    store.initialize()
    try:
        yield store
    finally:
        with store.repository.transaction(write=True) as tx:
            for sid in store.test_session_ids:
                for table_name in ("context_handles", "context_bindings", "context_receipts", "contexts"):
                    table = tx.tables[table_name]
                    tx.connection.execute(table.delete().where(table.c.session_id == sid))
                table = tx.tables["sessions"]
                tx.connection.execute(table.delete().where(table.c.id == sid))
            for tid in store.test_template_ids:
                table = tx.tables["context_templates"]
                tx.connection.execute(table.delete().where(table.c.id == tid))
        store.close()


@pytest.fixture
def client(test_store):
    from context_protocol.app import create_app
    with TestClient(create_app(test_store)) as client:
        yield client
