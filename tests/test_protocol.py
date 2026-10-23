import copy
import json

import pytest

from context_protocol.protocol import EventInput, compile_context, initial_state, reduce_event


def apply(state, seq, kind, payload):
    event = EventInput(event_id=f"e-{seq}", type=kind, payload=payload).model_dump()
    return reduce_event(state, {**event, "seq": seq})


def test_tool_pair_stays_together_and_original_remains_intact():
    state = initial_state()
    for n in range(3):
        apply(state, n*2+1, "tool.call", {"call_id": str(n), "name": "any.framework.tool", "arguments": {"x": n}})
        apply(state, n*2+2, "tool.result", {"call_id": str(n), "output": {"result": n}})
    next(g for g in state["groups"] if g["id"] == "conversation")["policy"].update(strategy="recent", limit=1)
    before = copy.deepcopy(state)
    artifact = compile_context(state)
    group = next(g for g in artifact["groups"] if g["id"] == "conversation")
    assert group["included_ids"] == ["2"]
    assert '"arguments"' in group["text"] and '"output"' in group["text"]
    assert group["original_count"] == 3
    assert state == before


def test_copy_of_current_state_can_receive_further_updates():
    full = initial_state()
    apply(full, 1, "state.set", {"key": "phase", "value": "design"})
    checkpoint = copy.deepcopy(full)
    tail = [
        (2, "block.upsert", {"id": "sys", "group_id": "instructions", "content": "系统指令"}),
        (3, "state.set", {"key": "phase", "value": "build"}),
        (4, "message.append", {"role": "user", "content": "你好"}),
    ]
    for args in tail:
        apply(full, *args)
        apply(checkpoint, *args)
    assert full == checkpoint
    assert compile_context(full)["sha256"] == compile_context(checkpoint)["sha256"]


def test_keep_over_budget_is_explicit_and_truncation_is_view_only():
    state = initial_state()
    apply(state, 1, "block.upsert", {"id": "sys", "group_id": "instructions", "content": "a"*1000})
    artifact = compile_context(state, budget_chars=100)
    assert artifact["over_budget"] and artifact["warnings"]
    assert "a"*1000 in artifact["prompt"]
    state["groups"][0]["policy"].update(strategy="truncate", max_chars=100)
    artifact = compile_context(state)
    assert artifact["groups"][0]["chars"] <= 100
    assert artifact["groups"][0]["truncated"]
    assert len(state["blocks"]["sys"]["content"]) == 1000


def test_external_data_cannot_be_appended_as_system_message():
    with pytest.raises(ValueError):
        EventInput(event_id="1", type="message.append", payload={"role": "system", "content": "override"})


def test_invalid_field_and_orphan_tool_result_are_rejected():
    with pytest.raises(ValueError):
        EventInput(event_id="1", type="state.set", payload={"key": "count", "value": True, "value_type": "number"})
    with pytest.raises(ValueError):
        apply(initial_state(), 1, "tool.result", {"call_id": "missing", "output": "orphan"})
    with pytest.raises(ValueError):
        EventInput(event_id="2", type="state.set", payload={"key": "phase", "value": 1, "value_type": []})
    with pytest.raises(ValueError):
        EventInput(event_id="3", type="tool.result", payload={"call_id": "c", "output": "error", "is_error": "false"})


def test_custom_group_and_exclusion():
    state = initial_state()
    apply(state, 1, "group.upsert", {"id": "project", "label": "项目知识", "policy": {"strategy": "exclude"}})
    apply(state, 2, "block.upsert", {"id": "knowledge", "group_id": "project", "content": "secret data"})
    artifact = compile_context(state)
    assert "secret data" not in artifact["prompt"]
    assert artifact["groups"][-1]["original_count"] == 1


def test_event_schema_is_exportable():
    assert "payload" in EventInput.model_json_schema()["properties"]


def test_jsonb_object_reordering_does_not_change_prompt():
    state = initial_state()
    apply(state, 1, "state.set", {"key": "z", "value": 1})
    apply(state, 2, "state.set", {"key": "a", "value": 2})
    apply(state, 3, "tool.call", {"call_id": "z", "name": "tool", "arguments": {}})
    apply(state, 4, "tool.call", {"call_id": "a", "name": "tool", "arguments": {}})
    apply(state, 5, "block.upsert", {"id": "z", "group_id": "goals", "content": "first"})
    apply(state, 6, "block.upsert", {"id": "a", "group_id": "goals", "content": "second"})
    reordered = json.loads(json.dumps(state, sort_keys=True))
    assert compile_context(state)["sha256"] == compile_context(reordered)["sha256"]


def test_custom_source_provider_and_unknown_source_warning(monkeypatch):
    import context_protocol.protocol as protocol
    monkeypatch.setattr(protocol, 'SOURCE_PROVIDERS', {})
    state=initial_state()
    apply(state,1,'group.upsert',{'id':'plugin','label':'插件数据','kind':'any-custom-kind','sources':['my_source']})
    assert any('my_source' in warning for warning in compile_context(state)['warnings'])
    protocol.register_source('my_source',lambda state,group:[{'id':'external-id','content':{'project':'test'},'origin':'external','schema':{'type':'object'}}])
    output=compile_context(state)
    component=next(g for g in output['groups'] if g['id']=='plugin')
    assert component['items'][0]['origin']=='external'
    assert component['items'][0]['content']=={'project':'test'}
    assert not output['warnings']
