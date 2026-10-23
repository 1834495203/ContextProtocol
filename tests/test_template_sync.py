"""Shared configuration changes and instance values have distinct lifetimes."""
from uuid import uuid4


def send(client, path, kind, payload, revision=None):
    return client.post(path+'/updates', json={'type':kind, 'payload':payload,
        'update_id':str(uuid4()), 'expected_revision':revision})


def setup_instances(client):
    template = client.post('/api/templates', json={'name':'scope tests'}).json()['template']['id']
    path = '/api/templates/'+template
    instances = [client.post('/api/sessions',json={'name':slug,'slug':slug,'template_id':template}).json()['id']
                 for slug in ('first','second')]
    return path, ['/api/sessions/'+sid for sid in instances]


def test_shared_prompts_and_definitions_sync_without_copying_runtime(client):
    template, (first, second) = setup_instances(client)
    assert send(client, first, 'state.define', {'key':'private','value':'local'}).status_code == 201
    assert send(client, first, 'item.upsert', {'id':'private','group_id':'goals','content':'local prompt'}).status_code == 201
    assert send(client, first, 'message.append', {'role':'user','content':'runtime history'}).status_code == 201
    assert send(client, template, 'state.define', {'key':'phase','value':'ready','value_type':'string','description':'shared status'}).status_code == 200
    assert send(client, first, 'state.set', {'key':'phase','value':'running'}).status_code == 201
    assert send(client, template, 'state.define', {'key':'phase','value':'new default','value_type':'string','description':'new description'}).status_code == 200
    for path in (first, second):
        state = client.get(path).json()['state']
        assert state['field_definitions']['phase']['value'] == 'new default'
        assert state['fields']['phase']['description'] == 'new description'
    assert client.get(first).json()['state']['fields']['phase']['value'] == 'running'
    assert client.get(second).json()['state']['fields']['phase']['value'] == 'ready'
    assert 'private' not in client.get(second).json()['state']['fields']
    assert send(client, template, 'item.upsert', {'id':'shared','group_id':'goals','content':'shared prompt'}).status_code == 200
    assert send(client, first, 'item.upsert', {'id':'shared','group_id':'goals','content':'local override'}).status_code == 201
    assert client.get(second).json()['state']['items']['shared']['content'] == 'shared prompt'
    assert send(client, template, 'item.upsert', {'id':'shared','group_id':'goals','content':'all instances'}).status_code == 200
    for path in (first, second):
        assert client.get(path).json()['state']['items']['shared']['content'] == 'all instances'
    state = client.get(first).json()['state']
    assert state['messages'][0]['content'] == 'runtime history'
    assert state['items']['private']['content'] == 'local prompt'
    assert send(client, template, 'state.delete', {'key':'phase'}).status_code == 200
    assert all('phase' not in client.get(p).json()['state']['fields'] for p in (first, second))


def test_definition_metadata_does_not_enter_model_input(client):
    template, (first, _) = setup_instances(client)
    send(client, template, 'state.define', {'key':'count','value':0,'value_type':'number'})
    send(client, first, 'state.set', {'key':'count','value':42})
    before = client.get(first+'/preview').json()['model_input']
    assert send(client, first, 'state.define', {'key':'count','value':99,'value_type':'number'}).status_code == 201
    after = client.get(first+'/preview').json()['model_input']
    assert before == after
    assert 'field_definitions' not in str(after)
    assert client.get(first).json()['state']['fields']['count']['value'] == 42
    assert client.get(template).json()['state']['fields']['count']['value'] == 0


def test_incompatible_definition_rolls_back_template_and_all_instances(client):
    template, instances = setup_instances(client)
    send(client, template, 'state.define', {'key':'phase','value':'ready','value_type':'string'})
    before = [client.get(path).json() for path in [template, *instances]]
    response = send(client, template, 'state.define', {'key':'phase','value':0,'value_type':'number'})
    assert response.status_code == 409
    assert 'phase' in response.json()['detail']
    assert before == [client.get(path).json() for path in [template, *instances]]


def test_template_order_and_components_sync_preserving_local_components(client):
    template, (first, second) = setup_instances(client)
    send(client, first, 'group.upsert', {'id':'private','label':'local'})
    send(client, template, 'group.upsert', {'id':'shared','label':'shared'})
    order = [g['id'] for g in reversed(client.get(template).json()['state']['groups'])]
    assert send(client, template, 'group.reorder', {'ids':order}).status_code == 200
    for path in (first,second):
        ids = [g['id'] for g in client.get(path).json()['state']['groups']]
        assert [key for key in ids if key != 'private'] == order
    assert 'private' in [g['id'] for g in client.get(first).json()['state']['groups']]
    old_revision = client.get(first).json()['revision']
    send(client, template, 'item.upsert', {'id':'rule','group_id':'shared','content':'new'})
    assert send(client, first, 'state.set', {'key':'x','value':1}, old_revision).status_code == 409


def test_template_prompt_collision_with_external_item_is_atomic(client):
    template, (first, second) = setup_instances(client)
    response = client.post(first+'/ingest',json={'type':'item.upsert','update_id':str(uuid4()),
        'payload':{'id':'rule','group_id':'instructions','origin':'external','content':'provider content'}})
    assert response.status_code == 201
    before = [client.get(path).json() for path in [template,first,second]]
    assert send(client, template, 'item.upsert', {'id':'rule','group_id':'instructions','content':'override'}).status_code == 409
    assert before == [client.get(path).json() for path in [template,first,second]]


def test_deleting_shared_prompt_cannot_delete_provider_replacement(client):
    template, (first, second) = setup_instances(client)
    send(client, template, 'item.upsert', {'id':'rule','group_id':'instructions','content':'initial'})
    client.post(first+'/ingest',json={'type':'item.upsert','update_id':str(uuid4()),
        'payload':{'id':'rule','group_id':'instructions','origin':'external','content':'provider replacement'}})
    before = [client.get(path).json() for path in [template,first,second]]
    assert send(client, template, 'item.delete', {'id':'rule'}).status_code == 409
    assert before == [client.get(path).json() for path in [template,first,second]]
