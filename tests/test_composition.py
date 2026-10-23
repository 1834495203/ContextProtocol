from uuid import uuid4


def create(client, name="composition-test"):
    return client.post('/api/sessions', json={'name': name}).json()['id']


def emit(client, sid, kind, payload, producer=False):
    body={'update_id':str(uuid4()),'type':kind,'payload':payload}
    return client.post(f'/api/sessions/{sid}/'+('ingest' if producer else 'updates'),json=body)


def test_custom_components_reorder_delete_and_structured_schema(client):
    sid=create(client)
    assert emit(client,sid,'group.upsert',{'id':'custom_policy','label':'团队规则','kind':'custom/domain-policy','sources':['items']}).status_code==201
    assert emit(client,sid,'item.upsert',{'id':'rule','group_id':'custom_policy','content':{'language':'中文'},'schema':{'type':'object'}}).status_code==201
    state=client.get(f'/api/sessions/{sid}').json()
    order=[g['id'] for g in state['state']['groups']]
    order.insert(0,order.pop())
    assert emit(client,sid,'group.reorder',{'ids':order}).status_code==201
    preview=client.get(f'/api/sessions/{sid}/preview').json()
    assert preview['groups'][0]['id']=='custom_policy'
    assert preview['envelope']['sections'][0]['items'][0]['schema']=={'type':'object'}
    assert emit(client,sid,'group.reorder',{'ids':['custom_policy']}).status_code==422
    # Every default component is removable; the compiler requires no magic IDs.
    for gid in order:
        assert emit(client,sid,'group.delete',{'id':gid}).status_code==201
    preview=client.get(f'/api/sessions/{sid}/preview').json()
    assert preview['groups']==[] and preview['prompt']==''


def test_external_tools_and_skills_are_read_only_on_editor_endpoint(client):
    sid=create(client)
    tool={'name':'external.search','description':'provided by host','input_schema':{'type':'object'}}
    assert emit(client,sid,'tool.register',tool,producer=True).status_code==201
    assert emit(client,sid,'tool.register',{**tool,'description':'overwrite'}).status_code==422
    forged={'update_id':str(uuid4()),'type':'tool.unregister','source':'trusted-agent','payload':{'name':tool['name'],'_context_actor':'producer'}}
    assert client.post(f'/api/sessions/{sid}/updates',json=forged).status_code==422
    assert emit(client,sid,'group.upsert',{'id':'skills','label':'外部 Skills','kind':'skills','origin':'external','sources':['items']},producer=True).status_code==201
    skill={'id':'skill1','group_id':'skills','content':'skill instructions','schema':{'type':'string'},'origin':'external','source_uri':'skill://example'}
    assert emit(client,sid,'item.upsert',skill,producer=True).status_code==201
    assert emit(client,sid,'item.upsert',{**skill,'origin':'internal','content':'overwrite'}).status_code==422
    assert emit(client,sid,'item.delete',{'id':'skill1'}).status_code==422
    assert emit(client,sid,'group.upsert',{'id':'skills','label':'unlock','origin':'internal'}).status_code==422
    assert emit(client,sid,'group.upsert',{'id':'skills','label':'新显示名称','enabled':False}).status_code==201
    stored=client.get(f'/api/sessions/{sid}').json()['state']
    assert stored['tools']['external.search']['description']=='provided by host'
    assert stored['items']['skill1']['content']=='skill instructions'




def test_editing_user_context_does_not_rewind_tool_observations(client):
    sid=create(client)
    emit(client,sid,'message.append',{'role':'user','content':'original'})
    emit(client,sid,'tool.call',{'call_id':'c1','name':'search','arguments':{}},producer=True)
    emit(client,sid,'tool.result',{'call_id':'c1','output':'completed observation'},producer=True)
    before=client.get(f'/api/sessions/{sid}').json()
    assert emit(client,sid,'message.update',{'message_seq':1,'content':'edited'}).status_code==201
    after=client.get(f'/api/sessions/{sid}').json()
    assert after['state']['messages'][0]['content']=='edited'
    assert after['state']['calls']==before['state']['calls']
    assert after['revision']==before['revision']+1
    preview=client.get(f'/api/sessions/{sid}/preview').json()
    conversation=next(g for g in preview['groups'] if g['id']=='conversation')
    assert conversation['input_messages'][0]['content']=='edited'
    assert any(m['role']=='tool' and 'completed observation' in m['content'] for m in conversation['input_messages'])
    emit(client,sid,'group.upsert',{'id':'conversation','label':'对话','enabled':False})
    hidden=client.get(f'/api/sessions/{sid}/preview').json()
    assert not any(m['role']=='tool' for m in hidden['model_input']['messages'])
    assert client.get(f'/api/sessions/{sid}').json()['state']['calls']==after['state']['calls']
