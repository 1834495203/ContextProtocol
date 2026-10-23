"""Behavioral contracts for the typed layers and extension seams."""
from pathlib import Path
import ast

import pytest
from pydantic import BaseModel, ValidationError

from context_protocol.models.commands import ContextUpdate, StateSetCommand, SetField
from context_protocol.models.context import Group, Policy, new_state
from context_protocol.models.compilation import DataSourceItem, CompiledContext
from context_protocol.models.persistence import InstanceInfo, InstanceView, ContextRow
from context_protocol.domain.compiler import ContextCompiler
from context_protocol.domain.sources.registry import default_sources
from context_protocol.domain.compression.strategies import default_compression, CompressionResult


def test_command_discriminator_selects_concrete_payload_and_checks_nested_values():
    request = ContextUpdate.model_validate({'update_id':'typed','type':'state.set',
        'payload':{'key':'project','value_type':'object','value':{'tasks':[{'name':'research','done':False}]}}})
    assert isinstance(request.root, StateSetCommand)
    assert isinstance(request.root.payload, SetField)
    assert request.root.payload.value['tasks'][0]['done'] is False
    with pytest.raises(ValidationError):
        ContextUpdate.model_validate({'update_id':'bad','type':'state.set','payload':{'key':'n','value_type':'number','value':True}})
    with pytest.raises(ValidationError):
        ContextUpdate.model_validate({'update_id':'bad','type':'tool.call','payload':{'name':'missing required fields'}})


def test_mapper_and_service_return_models_instead_of_rows(test_store):
    instance = test_store.create_session('typed layer contract')
    assert isinstance(instance, InstanceInfo)
    view = test_store.current(instance.id)
    assert isinstance(view, InstanceView)
    assert isinstance(test_store.build(instance.id), CompiledContext)
    with test_store.repository.transaction() as mapper:
        record = mapper.context(instance.id)
        assert isinstance(record, ContextRow)
        assert isinstance(record.state, BaseModel)


def test_pre_refactor_retry_receipts_remain_compatible(test_store):
    import hashlib
    import json
    from context_protocol.models.persistence import ReceiptRow

    instance = test_store.create_session('old receipt compatibility')
    legacy_wire = {'protocol_version':'0.1','update_id':'legacy-id','type':'state.set','source':'manual',
                   'turn_id':None,'payload':{'key':'phase','value':'running'},'actor':'editor'}
    digest = hashlib.sha256(json.dumps(legacy_wire,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with test_store.repository.transaction(write=True) as mapper:
        mapper.set_version(instance.id,1)
        mapper.insert_receipt(ReceiptRow(session_id=instance.id,update_id='legacy-id',digest=digest,revision=1))
    retry = test_store.update(instance.id,ContextUpdate(update_id='legacy-id',type='state.set',payload={'key':'phase','value':'running'}))
    assert retry.duplicate and retry.update.seq == 1
    assert test_store.current(instance.id).revision == 1


def test_openapi_exposes_concrete_requests_and_responses(client):
    document = client.get('/openapi.json').json()
    update = document['components']['schemas']['ContextUpdate']
    assert update['discriminator']['propertyName'] == 'type'
    assert 'state.set' in update['discriminator']['mapping']
    instances = document['paths']['/api/sessions']['get']['responses']['200']['content']['application/json']['schema']
    assert instances['items']['$ref'].endswith('/InstanceInfo')
    exports = document['paths']['/api/templates/{tid}/export']['get']['responses']['200']['content']['application/json']['schema']
    assert exports['$ref'].endswith('/TemplateExport')


def test_retrieval_source_and_new_compression_strategy_do_not_change_compiler():
    class RetrievalSource:
        name = 'retrieval'

        def extract(self, state, group):
            return [DataSourceItem(id='document-1',content='retrieved document',origin='external'),
                    DataSourceItem(id='document-2',content='unused document',origin='external')]

    class SelectFirstStrategy:
        name = 'first_document'

        def compress(self, items, group):
            return CompressionResult.selected(items[:1], 'retrieval selection')

    sources, compression = default_sources(), default_compression()
    sources.register(RetrievalSource())
    compression.register(SelectFirstStrategy())
    state = new_state()
    state.groups.append(Group(id='knowledge',label='检索资料',sources=['retrieval'],
                              policy=Policy(strategy='first_document')))
    output = ContextCompiler(sources=sources,compression=compression).compile(state)
    assert output.groups[-1].included_ids == ['document-1']
    assert 'retrieved document' in output.prompt and 'unused document' not in output.prompt
    assert state.groups[-1].sources == ['retrieval']
    assert not output.warnings


def test_controllers_do_not_access_database_or_define_payload_dictionaries():
    root = Path(__file__).resolve().parents[1]/'context_protocol'/'controllers'
    for file in root.glob('*.py'):
        tree = ast.parse(file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith(('sqlalchemy','psycopg','pymysql'))
            if isinstance(node, ast.Attribute):
                assert node.attr not in ('connection','execute','tables','transaction')


def test_business_services_return_dtos_and_do_not_index_structural_dictionary_keys():
    root = Path(__file__).resolve().parents[1]/'context_protocol'/'services'
    for file in root.glob('*.py'):
        tree = ast.parse(file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                assert not isinstance(node.value, ast.Dict), f'{file.name} returns an untyped business dictionary'
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                assert node.slice.value not in ('state','payload','revision','session_id','template_id','version'), file.name
