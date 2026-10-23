from concurrent.futures import ThreadPoolExecutor

from context_protocol.client import ContextClient
from context_protocol.protocol import ContextUpdate


def template(client, name='named instances'):
    return client.post('/api/templates', json={'name':name}).json()['template']['id']


def create(client, tid, slug='main', name='display name', agent_id=None):
    return client.post(f'/api/templates/{tid}/instances', json={'slug':slug,'name':name,'agent_id':agent_id})


def test_instance_names_are_unique_within_a_template(client):
    first, second = template(client,'first'), template(client,'second')
    a = create(client, first).json()
    b = create(client, second).json()
    assert a['slug'] == b['slug'] == 'main' and a['id'] != b['id']
    assert create(client, first).status_code == 409
    assert create(client, first, slug='UPPER').status_code == 422
    assert [r['id'] for r in client.get(f'/api/templates/{first}/instances').json()] == [a['id']]
    current = client.get(f'/api/templates/{first}/instances/main').json()
    assert current['session']['id'] == a['id'] and current['session']['slug'] == 'main'
    assert client.get(f'/api/templates/{first}/instances/absent').status_code == 404


def test_connect_reuses_existing_state_and_never_overwrites_it_with_template(client):
    tid = template(client)
    client.base_url = 'http://testserver/api/'
    first = ContextClient.connect(client,tid,'researcher',agent_id='agent-a')
    first.emit('state.set', {'key':'phase','value':'in progress'})
    second = ContextClient.connect(client,tid,'researcher',agent_id='agent-a',create=False)
    assert first.session_id == second.session_id
    assert second.state()['state']['fields']['phase']['value'] == 'in progress'
    response = client.post(f'/templates/{tid}/instances/connect', json={'slug':'missing','create':False})
    assert response.status_code == 404
    assert len(client.get(f'/templates/{tid}/instances').json()) == 1
    assert client.post(f'/templates/{tid}/instances/connect', json={'slug':'researcher','agent_id':'other-agent'}).status_code == 409


def test_concurrent_connect_creates_exactly_one_instance(client, test_store):
    tid = template(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: test_store.connect_instance(tid,'shared',agent_id='same-agent'), range(8)))
    assert len({r.instance.id for r in results}) == 1
    assert sum(r.created for r in results) == 1
    assert len(test_store.template_instances(tid)) == 1


def test_instance_metadata_can_change_names_without_touching_context(client):
    tid = template(client)
    sid = create(client,tid).json()['id']
    path = '/api/sessions/'+sid
    before = client.get(path).json()
    response = client.post(path+'/metadata',json={'name':'new name','slug':'coder','agent_id':'coder-agent','expected_revision':0})
    assert response.status_code == 200
    current = response.json()
    assert current['state'] == before['state']
    assert current['session']['name'] == 'new name' and current['binding']['slug'] == 'coder'
    assert client.get(f'/api/templates/{tid}/instances/main').status_code == 404
    assert client.get(f'/api/templates/{tid}/instances/coder').json()['session']['id'] == sid
    assert client.post(path+'/metadata',json={'name':'stale','slug':'old','expected_revision':0}).status_code == 409


def test_legacy_assignment_preserves_all_context_data(client, test_store):
    tid = template(client)
    sid = test_store.create_session('legacy').id
    test_store.update(sid,ContextUpdate(update_id='original',type='state.set',payload={'key':'result','value':42}))
    with test_store.repository.transaction(write=True) as tx:
        for name in ('context_handles','context_bindings'):
            table = tx.tables[name]
            tx.connection.execute(table.delete().where(table.c.session_id==sid))
    before = test_store.current(sid)
    assert sid in [r['id'] for r in client.get('/api/instances/unassigned').json()]
    assigned = client.post(f'/api/sessions/{sid}/assign',json={'template_id':tid,'slug':'legacy','agent_id':'legacy-agent'})
    assert assigned.status_code == 200
    assert assigned.json()['state'] == before.state.model_dump(mode='json', by_alias=True) and assigned.json()['revision'] == before.revision
    assert assigned.json()['binding']['origin'] == 'assigned'
    assert sid not in [r['id'] for r in client.get('/api/instances/unassigned').json()]
    assert client.post(f'/api/sessions/{sid}/assign',json={'template_id':tid,'slug':'other'}).status_code == 409


def test_existing_binding_gets_a_stable_slug_on_upgrade(client, test_store):
    tid = template(client)
    sid = create(client,tid,'previous').json()['id']
    before = test_store.current(sid)
    with test_store.repository.transaction(write=True) as tx:
        table = tx.tables['context_handles']
        tx.connection.execute(table.delete().where(table.c.session_id==sid))
    test_store.initialize()
    current = test_store.current(sid)
    slug = current.binding.slug
    assert slug and current.state == before.state and current.revision == before.revision
    test_store.initialize()
    assert test_store.current(sid).binding.slug == slug
