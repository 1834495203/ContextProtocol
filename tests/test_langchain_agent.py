"""Real LangChain + HTTP API + configured storage integration, no paid model."""
import json

import pytest

pytest.importorskip("langchain")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from context_protocol.protocol import compile_context, initial_state, reduce_event
from examples.langchain_agent import ContextAgent, ContextClient, ScriptedModel, render_messages


@pytest.fixture
def context(client):
    client.base_url = "http://testserver/api/"
    session = client.post("/sessions", json={"name": "langchain-integration-test"}).json()
    return ContextClient(client, session["id"])


def test_real_agent_loop_and_fresh_agent_recall(context):
    model = ScriptedModel()
    agent = ContextAgent(context, model)
    assert "42" in agent.run("计算 6 乘 7，并保存结果。")
    assert len(model._requests) == 3
    state = context.state()["state"]
    assert state["fields"]["last_result"]["value"] == 42
    assert {c["name"] for c in state["calls"].values()} == {"multiply", "context.state.set"}
    assert all(c["status"] == "completed" for c in state["calls"].values())
    # At request #2 the multiply call and result are native paired messages.
    assert any(isinstance(m, AIMessage) and m.tool_calls for m in model._requests[1])
    assert any(isinstance(m, ToolMessage) and m.name == "multiply" for m in model._requests[1])
    assert "last_result" in str(model._requests[2])
    assert state['requests'] == {}  # no model request archive or replay
    fresh_model = ScriptedModel()
    fresh_agent = ContextAgent(context, fresh_model)
    assert "42" in fresh_agent.run("上一轮保存的 last_result 是多少？")
    assert len(fresh_model._requests) == 1
    assert len(context.state()["state"]["calls"]) == 2


def test_recent_policy_really_removes_old_agent_history(context):
    context.emit("message.append", {"role": "user", "content": "OLD_SENTINEL_SHOULD_BE_EVICTED"})
    context.emit("group.upsert", {"id": "conversation", "label": "对话", "policy": {"strategy": "recent", "limit": 1}})
    model = ScriptedModel()
    ContextAgent(context, model).run("计算 6 乘 7 并保存。")
    assert all("OLD_SENTINEL_SHOULD_BE_EVICTED" not in str(request) for request in model._requests)
    # Original history survives in PG even though the model no longer receives it.
    assert any(m["content"] == "OLD_SENTINEL_SHOULD_BE_EVICTED" for m in context.state()["state"]["messages"])


class BadStateModel(ScriptedModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            assert messages[-1].status == "error"
            answer = AIMessage(content="字段更新失败，类型不匹配。")
        else:
            answer = AIMessage(content="", tool_calls=[{"id": "bad-state", "name": "context_state_set",
                "args": {"key": "bad", "value": "not a number", "value_type": "number"}, "type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=answer)])


def test_failed_tool_has_error_result_and_does_not_update_state(context):
    assert "失败" in ContextAgent(context, BadStateModel()).run("尝试一次字段更新。")
    state = context.state()["state"]
    assert "bad" not in state["fields"]
    assert state["calls"]["bad-state"]["status"] == "error"


def test_native_tool_schema_comes_from_synchronized_external_catalog(context):
    model=ScriptedModel()
    agent=ContextAgent(context,model)
    schema={'type':'object','properties':{'a':{'type':'integer','description':'host schema'},'b':{'type':'integer'}},'required':['a','b']}
    context.emit('tool.register',{'name':'multiply','description':'external producer revision','input_schema':schema})
    agent.run('计算并保存 6 乘 7。')
    tool=next(t for t in context.build()['model_input']['tools'] if t['name']=='multiply')
    assert tool['description']=='external producer revision'
    assert tool['input_schema']==schema
    actual=next(t['function'] for t in model._tool_requests[0] if t['function']['name']=='multiply')
    assert actual['description']==tool['description'] and actual['parameters']==schema


def test_rendering_keeps_parallel_tool_batches_valid_when_conversation_excluded():
    state = initial_state()
    events = [
        ("message.append", {"role": "assistant", "content": "计算中"}),
        ("tool.call", {"call_id": "c1", "name": "multiply", "arguments": {"a": 2, "b": 3}, "assistant_seq": 1}),
        ("tool.call", {"call_id": "c2", "name": "multiply", "arguments": {"a": 3, "b": 4}, "assistant_seq": 1}),
        ("tool.result", {"call_id": "c2", "output": 12}),
        ("tool.result", {"call_id": "c1", "output": 6}),
    ]
    for seq, (kind, payload) in enumerate(events, 1):
        reduce_event(state, {"seq": seq, "type": kind, "payload": payload})
    next(g for g in state["groups"] if g["id"] == "conversation")["enabled"] = False
    state["groups"].append({"id":"calls_only","label":"工具记录","role":"conversation","sources":["calls"]})
    artifact = compile_context(state)
    _, messages = render_messages(artifact, state)
    native = [m for m in messages if isinstance(m, (AIMessage, ToolMessage))]
    assert [c["id"] for c in native[0].tool_calls] == ["c1", "c2"]
    assert [m.tool_call_id for m in native[1:]] == ["c1", "c2"]
    state["groups"][-1]["enabled"] = False
    _, messages = render_messages(compile_context(state), state)
    assert not any(isinstance(m, (AIMessage, ToolMessage)) for m in messages)


def test_truncated_conversation_uses_only_the_truncated_view():
    state = initial_state()
    reduce_event(state, {"seq": 1, "type": "message.append", "payload": {"role": "user", "content": "a"*1000 + "HIDDEN_TAIL"}})
    state["groups"][4]["policy"].update(strategy="truncate", max_chars=100)
    _, messages = render_messages(compile_context(state), state)
    assert "HIDDEN_TAIL" not in str(messages)
    assert "组件视图已截断" in str(messages)


def test_ui_input_and_langchain_messages_share_order_and_compression(context):
    context.emit('block.upsert', {'id':'system','group_id':'instructions','content':'SYSTEM_MARKER'})
    context.emit('message.append', {'role':'user','content':'HIDDEN_OLD'}, 'old')
    context.emit('message.append', {'role':'user','content':'VISIBLE_CURRENT'}, 'current')
    context.emit('group.upsert', {'id':'conversation','label':'对话','policy':{'strategy':'recent','limit':1}})
    current = context.state()['state']
    order = [g['id'] for g in current['groups']]
    order.insert(0, order.pop(order.index('conversation')))
    context.emit('group.reorder', {'ids': order})
    artifact = context.build()
    _, rendered = render_messages(artifact)
    assert [m.content for m in rendered] == [m['content'] for m in artifact['model_input']['messages']]
    assert rendered[0].content == 'VISIBLE_CURRENT'
    assert 'HIDDEN_OLD' not in str(rendered)
    assert [m for g in artifact['groups'] for m in g['input_messages']] == artifact['model_input']['messages']
