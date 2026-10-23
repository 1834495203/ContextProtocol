from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import subprocess
import sys

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable

from context_protocol.protocol import ContextUpdate
from context_protocol.settings import Settings
from context_protocol.storage.base import StorageError
from context_protocol.storage.factory import create_repository
from context_protocol.storage.sql import SQLUnitOfWork, make_tables
from context_protocol.store import Store


def event(update_id="e1"):
    return ContextUpdate(update_id=update_id, type="state.set", payload={"key": "result", "value": {"answer": 42, "中文": [True, None]}})


def test_runtime_never_falls_back_to_unrelated_database(monkeypatch):
    monkeypatch.delenv("CONTEXT_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/novel")
    assert "novel" not in Settings.from_env().database_url
    monkeypatch.setenv("CONTEXT_DATABASE_URL", "sqlite:///context.db")
    assert Settings.from_env().database_url == "sqlite:///context.db"


def test_sqlite_application_does_not_import_server_database_drivers():
    script = '''
import sys
class BlockServerDrivers:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('psycopg', 'pymysql'):
            raise AssertionError('Unexpected server database driver import: ' + fullname)
sys.meta_path.insert(0, BlockServerDrivers())
from context_protocol.app import create_app
from context_protocol.storage.factory import create_repository
from context_protocol.store import Store
from fastapi.testclient import TestClient
with TestClient(create_app(Store(create_repository('sqlite:///:memory:')))) as client:
    assert client.get('/api/health').json()['database'] == 'sqlite'
    assert client.post('/api/sessions', json={'name': 'driver isolation'}).status_code == 201
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect(), mysql.dialect()])
def test_schema_compiles_without_postgres_only_sql(dialect):
    _, tables = make_tables()
    for table in tables.values():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl
        assert "context_protocol." not in ddl
    if dialect.name == "mysql":
        ddl = str(CreateTable(tables["context_receipts"]).compile(dialect=dialect))
        assert "VARBINARY" in ddl and "InnoDB" in ddl


def test_identity_and_json_are_preserved(test_store):
    sid = test_store.create_session("identity-test").id
    for name in ("Repeated", "repeated", "repeated ", "事件😀"):
        test_store.update(sid, event(name))
    assert test_store.current(sid).revision == 4
    assert test_store.current(sid).state.fields["result"].value["中文"] == [True, None]
    assert test_store.update(sid, event("repeated ")).duplicate


def test_receipt_failure_rolls_back_context_and_revision(test_store, monkeypatch):
    sid = test_store.create_session("rollback-test").id
    def fail(self, row):
        raise StorageError("injected receipt write failure")
    with monkeypatch.context() as patch:
        patch.setattr(SQLUnitOfWork, "insert_receipt", fail)
        with pytest.raises(StorageError):
            test_store.update(sid, event())
    assert test_store.current(sid).revision == 0
    assert "result" not in test_store.current(sid).state.fields
    assert test_store.update(sid, event()).update.seq == 1


def test_concurrent_duplicate_events_are_idempotent(test_store):
    sid = test_store.create_session("duplicate-race-test").id
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: test_store.update(sid, event()), range(8)))
    assert sum(not row.duplicate for row in results) == 1
    assert test_store.current(sid).revision == 1


def test_independent_sqlite_engines_share_writer_lock(tmp_path):
    url = "sqlite:///" + (tmp_path / "concurrent.db").as_posix()
    stores = [Store(create_repository(url)) for _ in range(3)]
    stores[0].initialize()
    sid = stores[0].create_session("cross-engine-lock").id
    try:
        def write(n):
            return stores[n % 3].update(sid, event(str(n))).update.seq
        with ThreadPoolExecutor(max_workers=6) as pool:
            assert sorted(pool.map(write, range(12))) == list(range(1, 13))
    finally:
        for store in stores:
            store.close()




def test_legacy_selected_context_is_imported_once_without_future_replay(tmp_path):
    from uuid import uuid4
    from sqlalchemy import select, func, inspect
    from context_protocol.protocol import initial_state, reduce_event
    from context_protocol.storage.legacy_sql import make_tables as legacy_tables
    repository = create_repository('sqlite:///' + (tmp_path/'legacy.db').as_posix())
    metadata, old = legacy_tables()
    metadata.create_all(repository.engine)
    root, child = str(uuid4()), str(uuid4())
    timestamp = datetime.now(timezone.utc)
    parent_state = initial_state()
    reduce_event(parent_state, {'seq':1, 'type':'state.set', 'payload':{'key':'value','value':'parent'}})
    child_state = initial_state()
    reduce_event(child_state, {'seq':2, 'type':'state.set', 'payload':{'key':'value','value':'selected child'}})
    with repository.engine.begin() as conn:
        for sid, revision in ((root,1),(child,2)):
            conn.execute(old['sessions'].insert().values(id=sid,name=sid,version=revision,created_at=timestamp))
        conn.execute(old['branches'].insert().values(id=root,root_id=root,parent_id=None,base_seq=0,name='main',created_at=timestamp))
        conn.execute(old['branches'].insert().values(id=child,root_id=root,parent_id=root,base_seq=1,name='old branch',created_at=timestamp))
        for sid, revision, state in ((root,1,parent_state),(child,2,child_state)):
            conn.execute(old['snapshots'].insert().values(session_id=sid,seq=revision,state=state,created_at=timestamp))
        conn.execute(old['context_selections'].insert().values(root_id=root,branch_id=child,seq=None,updated_at=timestamp))
    store = Store(repository)
    store.initialize()
    assert [s.id for s in store.sessions()] == [root]
    assert store.current(root).state.fields['value'].value == 'selected child'
    store.update(root, ContextUpdate(update_id='new',type='state.set',payload={'key':'value','value':'edited current'}))
    store.initialize()
    assert store.current(root).state.fields['value'].value == 'edited current'
    with repository.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(old['events'])).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(old['snapshots'])).scalar_one() == 2
    store.close()


def test_fresh_database_creates_no_history_tables(tmp_path):
    from sqlalchemy import inspect
    repository = create_repository('sqlite:///' + (tmp_path/'fresh.db').as_posix())
    repository.initialize()
    assert set(inspect(repository.engine).get_table_names()) == {'sessions','contexts','context_receipts','context_templates','context_bindings','context_handles'}
    repository.close()
