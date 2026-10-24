"""Backend-neutral contract checks, parametrized by tests/conftest.py."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from context_protocol.protocol import ContextUpdate
from context_protocol.storage.base import StorageError


@pytest.fixture
def sid(client):
    session_id = client.post("/api/sessions", json={"name": "integration-test-" + str(uuid4())}).json()["id"]
    return session_id


def event(n, kind="state.set", payload=None):
    return {"update_id": f"event-{n}", "type": kind, "source": "test", "payload": payload or {"key": "phase", "value": n}}


def test_idempotency_conflict_and_rollback(client, sid):
    path = f"/api/sessions/{sid}"
    assert client.post(path+"/updates", json={**event(1), "expected_revision": 0}).status_code == 201
    retry = client.post(path+"/updates", json={**event(1), "expected_revision": 0})
    assert retry.json()["duplicate"]
    assert client.post(path+"/updates", json=event(1, payload={"key": "other", "value": 9})).status_code == 409
    assert client.post(path+"/updates", json={**event(2), "expected_revision": 0}).status_code == 409
    assert client.post(path+"/updates", json=event(3,"tool.result", {"call_id":"missing","output":0})).status_code == 422
    assert client.get(path).json()["revision"] == 1


def test_current_context_is_overwritten_and_compile_is_read_only(client, sid, test_store):
    path = f"/api/sessions/{sid}"
    for n in range(1, 6):
        assert client.post(path+"/updates", json=event(n)).status_code == 201
    first = client.post(path+"/compile", json={}).json()
    second = client.post(path+"/compile", json={}).json()
    assert first == second and first["revision"] == 5
    assert "id" not in first and "branch_id" not in first and "seq" not in first
    exported = client.get(path+"/export").json()
    assert exported["state"]["fields"]["phase"]["value"] == 5
    assert "events" not in exported and "builds" not in exported
    assert exported["compiled"]["model_input"] == first["model_input"]
    with test_store.repository.transaction() as tx:
        assert tx.context(sid).state.model_dump(mode="json", by_alias=True) == exported["state"]
        assert set(tx.tables) == {"sessions", "contexts", "context_receipts", "context_templates", "context_bindings", "context_handles", "tool_sources"}


def test_concurrent_sequence_allocation(client, sid, test_store):
    def write(n):
        return test_store.update(sid, ContextUpdate.model_validate(event(n))).update.seq
    with ThreadPoolExecutor(max_workers=4) as pool:
        seqs = list(pool.map(write,range(8)))
    assert sorted(seqs) == list(range(1,9))


def test_removed_history_routes_and_parameters_are_rejected(client, sid):
    path = f"/api/sessions/{sid}"
    for resource in ("events", "branches", "selection", "snapshots", "builds"):
        assert client.get(path+"/"+resource).status_code == 404
        assert client.post(path+"/"+resource, json={}).status_code == 404
    for resource in ("", "/preview", "/export"):
        for parameter in ("seq=0", "branch_id="+sid):
            assert client.get(path+resource+"?"+parameter).status_code == 422
    assert client.post(path+"/compile", json={"seq":0}).status_code == 422
    assert client.post(path+"/updates", json={**event(1), "branch_id":sid}).status_code == 422
    assert client.get('/api/sessions/'+str(uuid4())).status_code == 404
    assert client.post(path+"/updates", json=event(4,"group.upsert",{"id":"../bad","label":"bad"})).status_code == 422
    schema = client.get('/api/protocol').json()['update_schema']
    assert schema['discriminator']['propertyName'] == 'type'
    assert all('event_id' not in entry.get('properties', {}) for entry in schema['$defs'].values())


def test_health_reports_injected_backend_and_errors_hide_driver_details(client, test_store, monkeypatch):
    health = client.get('/api/health').json()
    assert health['database'] == test_store.backend
    assert health['storage']['backend'] == test_store.backend
    assert 'password' not in health['storage']

    def fail():
        raise StorageError('example-password-must-not-be-returned')

    monkeypatch.setattr(test_store, 'sessions', fail)
    response = client.get('/api/sessions')
    assert response.status_code == 503
    assert 'example-password' not in response.text
