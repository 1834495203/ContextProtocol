from uuid import uuid4

import pytest

from context_protocol.client import ContextClient
from context_protocol.protocol import ContextUpdate


def create(client, **kwargs):
    response = client.post('http://testserver/api/templates', json={'name': 'test template', **kwargs})
    assert response.status_code == 201, response.text
    return response.json()['template']['id']


def update(client, tid, kind, payload, revision=None):
    return client.post(f'/api/templates/{tid}/updates', json={
        'update_id': str(uuid4()), 'type': kind, 'payload': payload, 'expected_revision': revision})


def test_multiple_templates_and_independent_instances(client):
    a = create(client, name='research')
    b = create(client, name='coding')
    for tid, text in ((a, 'RESEARCH_RULE'), (b, 'CODING_RULE')):
        assert update(client, tid, 'block.upsert', {'id':'policy','group_id':'instructions','content':text}).status_code == 200
    assert {a, b}.issubset({t['id'] for t in client.get('/api/templates').json()})
    client.base_url = 'http://testserver/api/'
    first = ContextClient.from_template(client, a, name='agent A context', agent_id='agent-A')
    second = ContextClient.from_template(client, a, name='agent B context', agent_id='agent-B')
    third = ContextClient.from_template(client, b, name='agent C context', agent_id='agent-C')
    assert len({first.session_id, second.session_id, third.session_id}) == 3
    assert 'RESEARCH_RULE' in first.build()['prompt'] and 'RESEARCH_RULE' in second.build()['prompt']
    assert 'CODING_RULE' in third.build()['prompt'] and 'RESEARCH_RULE' not in third.build()['prompt']
    first.emit('state.set', {'key':'private','value':'A_ONLY'})
    first.emit('tool.call', {'call_id':'same-call-id','name':'search','arguments':{}})
    first.emit('tool.result', {'call_id':'same-call-id','output':'A_TOOL_OUTPUT'})
    second.emit('tool.call', {'call_id':'same-call-id','name':'search','arguments':{}})
    second.emit('tool.result', {'call_id':'same-call-id','output':'B_TOOL_OUTPUT'})
    assert 'private' not in second.state()['state']['fields']
    assert second.state()['state']['calls']['same-call-id']['output'] == 'B_TOOL_OUTPUT'
    assert first.state()['binding']['agent_id'] == 'agent-A'
    assert 'A_TOOL_OUTPUT' not in third.build()['prompt']
    template = client.get('/templates/'+a).json()
    assert template['state']['calls'] == {} and template['state']['fields'] == {}


def test_template_definitions_sync_while_preserving_existing_current_values(client):
    tid = create(client)
    update(client, tid, 'state.set', {'key':'phase','value':'default','value_type':'string'})
    first = client.post('/api/sessions', json={'name':'first','template_id':tid}).json()['id']
    update(client, tid, 'state.set', {'key':'phase','value':'changed','value_type':'string'})
    second = client.post('/api/sessions', json={'name':'second','template_id':tid}).json()['id']
    assert client.get('/api/sessions/'+first).json()['state']['fields']['phase']['value'] == 'default'
    assert client.get('/api/sessions/'+second).json()['state']['fields']['phase']['value'] == 'changed'
    assert client.get('/api/sessions/'+first).json()['state']['field_definitions']['phase']['value'] == 'changed'
    assert client.get('/api/sessions/'+first).json()['binding']['template_revision'] == 2
    assert client.get('/api/sessions/'+second).json()['binding']['template_revision'] == 2


def test_template_configuration_copies_order_schema_policies_and_defaults(client):
    tid = create(client)
    update(client, tid, 'group.upsert', {'id':'custom','label':'custom','kind':'custom-kind','sources':['items'],
                                      'policy':{'strategy':'truncate','max_chars':100}})
    update(client, tid, 'item.upsert', {'id':'item','group_id':'custom','content':{'lang':'zh'},'schema':{'type':'object'}})
    state = client.get('/api/templates/'+tid).json()['state']
    order = [g['id'] for g in reversed(state['groups'])]
    update(client, tid, 'group.reorder', {'ids':order})
    copy_id = create(client, source_template_id=tid)
    sid = client.post('/api/sessions', json={'name':'instance','template_id':copy_id}).json()['id']
    instance = client.get('/api/sessions/'+sid).json()['state']
    assert [g['id'] for g in instance['groups']] == order
    assert instance['items']['item']['schema'] == {'type':'object'}
    assert instance['groups'][0]['policy']['max_chars'] == 100
    update(client, copy_id, 'item.delete', {'id':'item'})
    assert 'item' in client.get('/api/templates/'+tid).json()['state']['items']
    assert 'item' not in client.get('/api/sessions/'+sid).json()['state']['items']


def test_saving_instance_as_template_excludes_runtime_data(client):
    sid = client.post('/api/sessions', json={'name':'runtime'}).json()['id']
    client.base_url = 'http://testserver/api/'
    context = ContextClient(client, sid)
    context.emit('block.upsert', {'id':'policy','group_id':'instructions','content':'REUSABLE_RULE'})
    context.emit('state.set', {'key':'last_result','value':42,'value_type':'number','description':'result'})
    context.emit('message.append', {'role':'user','content':'PRIVATE_MESSAGE'})
    context.emit('tool.register', {'name':'private_tool','description':'PRIVATE_TOOL','input_schema':{}})
    context.emit('tool.call', {'call_id':'c','name':'private_tool','arguments':{}})
    context.emit('tool.result', {'call_id':'c','output':'PRIVATE_RESULT'})
    context.emit('memory.append', {'content':'PRIVATE_MEMORY'})
    context.emit('group.upsert', {'id':'skills','label':'skills','origin':'external','sources':['items']})
    context.emit('item.upsert', {'id':'s','group_id':'skills','origin':'external','content':'PRIVATE_SKILL'})
    tid = create(client, source_context_id=sid)
    response = client.get('/templates/'+tid)
    state = response.json()['state']
    assert state['fields']['last_result']['value'] == 0
    assert state['fields']['last_result']['value_type'] == 'number'
    assert state['blocks']['policy']['content'] == 'REUSABLE_RULE'
    assert state['messages'] == [] and state['calls'] == {} and state['memories'] == []
    assert not any(marker in response.text for marker in ('PRIVATE_MESSAGE','PRIVATE_TOOL','PRIVATE_RESULT','PRIVATE_MEMORY','PRIVATE_SKILL'))
    assert any(g['id']=='skills' for g in state['groups'])


def test_templates_reject_observations_and_detect_stale_edits(client):
    tid = create(client)
    assert update(client, tid, 'message.append', {'role':'user','content':'runtime'}).status_code == 422
    assert update(client, tid, 'tool.register', {'name':'t','description':'t','input_schema':{}}).status_code == 422
    assert update(client, tid, 'state.set', {'key':'phase','value':'one'}, 0).status_code == 200
    assert update(client, tid, 'state.set', {'key':'phase','value':'two'}, 0).status_code == 409
    assert client.post(f'/api/templates/{tid}/metadata', json={'name':'renamed','description':'purpose','expected_revision':1}).status_code == 200
    assert client.get('/api/templates/'+tid).json()['template']['name'] == 'renamed'
    assert client.post('/api/sessions', json={'name':'invalid','template_id':str(uuid4())}).status_code == 404
    assert client.post('/api/sessions', json={'name':'invalid','template_id':tid,'demo':True}).status_code == 422
    assert client.post('/api/templates', json={'name':'bad','source_template_id':tid,'source_context_id':str(uuid4())}).status_code == 422
    exported = client.get(f'/api/templates/{tid}/export').json()
    assert exported['format'] == 'context-template/0.4'
    assert not any(k in exported for k in ('events','branches','snapshots'))


def test_two_langchain_agents_select_same_template_without_sharing_runtime(client):
    pytest.importorskip('langchain')
    from examples.langchain_agent import ContextAgent, ScriptedModel
    tid = create(client)
    client.base_url = 'http://testserver/api/'
    first = ContextClient.from_template(client, tid, name='first Agent', agent_id='A')
    second = ContextClient.from_template(client, tid, name='second Agent', agent_id='B')
    assert '42' in ContextAgent(first, ScriptedModel()).run('计算 6 乘 7 并保存结果。')
    assert second.state()['state']['calls'] == {} and second.state()['state']['fields'] == {}
    assert '42' in ContextAgent(second, ScriptedModel()).run('计算 6 乘 7 并保存结果。')
    assert first.state()['state']['fields']['last_result']['value'] == 42
    assert second.state()['state']['fields']['last_result']['value'] == 42
    assert client.get('/templates/'+tid).json()['state']['calls'] == {}
