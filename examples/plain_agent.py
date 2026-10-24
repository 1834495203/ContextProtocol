"""Raw HTTP host; imports no context_protocol or agent framework.

Run against the local server: python -m examples.plain_agent
The scripted model makes this demonstration deterministic and free of model fees.
Replace model(messages, tools) with a provider adapter to use a real model.
"""
import argparse
import json
from uuid import uuid4
import httpx


def emit(http, sid, kind, payload, turn_id):
    body = {"protocol_version": "0.1", "update_id": str(uuid4()), "source": "plain-python",
            "type": kind, "payload": payload, "turn_id": turn_id}
    # Reuse the exact body/update_id for transport retries.
    for attempt in range(2):
        try:
            response = http.post(f"/sessions/{sid}/ingest", json=body)
            response.raise_for_status()
            return response.json()["update"]
        except httpx.TransportError:
            if attempt:
                raise


def run_turn(http, sid, prompt, model, tools, max_steps=10):
    turn_id = str(uuid4())
    emit(http, sid, "message.append", {"role": "user", "content": prompt}, turn_id)
    for _ in range(max_steps):
        response = http.post(f"/sessions/{sid}/prepare", json={
            "supported_versions": ["0.1"], "capabilities": {
                "roles": ["system", "user", "assistant", "tool"], "tool_calls": True}})
        response.raise_for_status()
        prepared = response.json()
        if not prepared["ready"]:
            raise ValueError(prepared["issues"])
        model_input = prepared["compiled"]["model_input"]
        executable = tools
        offered = [tool for tool in model_input["tools"] if tool["name"] in executable]
        answer = model(model_input["messages"], offered)
        calls = answer.get("tool_calls", [])
        receipt = emit(http, sid, "message.append", {"role": "assistant", "content": answer.get("content", ""),
            "tool_call_ids": [call["id"] for call in calls]}, turn_id)
        # Persist the whole call batch before executing any tools.
        for call in calls:
            emit(http, sid, "tool.call", {"call_id": call["id"], "name": call["name"],
                "arguments": call["arguments"], "assistant_seq": receipt["seq"]}, turn_id)
        for call in calls:
            try:
                if call["name"] not in {spec["name"] for spec in offered}:
                    raise ValueError("Model requested a tool that was not offered")
                output = executable[call["name"]](**call["arguments"])
                failed = False
            except Exception as exc:
                output, failed = {"error": type(exc).__name__}, True
            emit(http, sid, "tool.result", {"call_id": call["id"], "output": output, "is_error": failed}, turn_id)
        if not calls:
            return answer.get("content", "")
    raise RuntimeError("Host step limit reached")


class ScriptedModel:
    def __init__(self):
        self.step = 0

    def __call__(self, messages, tools):
        self.step += 1
        if self.step == 1:
            return {"tool_calls": [{"id": str(uuid4()), "name": "multiply", "arguments": {"a": 6, "b": 7}}]}
        if self.step == 2:
            result = next(m for m in reversed(messages) if m["role"] == "tool")
            return {"tool_calls": [{"id": str(uuid4()), "name": "context.state.set",
                "arguments": {"key": "last_result", "value": json.loads(result["content"]), "value_type": "number"}}]}
        return {"content": "Saved last_result=42"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010/api")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url) as http:
        discovery = http.get("/protocol")
        discovery.raise_for_status()
        if "0.1" not in discovery.json()["host_protocol_versions"]:
            raise RuntimeError("Incompatible host protocol")
        response = http.post("/sessions", json={"name": "Plain HTTP host"})
        response.raise_for_status()
        sid = response.json()["id"]
        state_tool = next(tool for tool in discovery.json()['context_tools'] if tool['name'] == 'context.state.set')
        emit(http, sid, 'tool.register', {'name': state_tool['name'], 'description': state_tool['description'],
            'input_schema': state_tool['input_schema'], 'tool_ref': 'context/'+state_tool['name']}, None)
        emit(http, sid, "tool.register", {"name": "multiply", "description": "Multiply two integers",
            "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                             "required": ["a", "b"]}}, None)
        print(run_turn(http, sid, "Multiply 6 by 7 and save last_result", ScriptedModel(), {
            "multiply": lambda a, b: a*b,
            state_tool['name']: lambda **args: emit(http, sid, 'state.set', args, None)}))
        print("Context:", sid)


if __name__ == "__main__":
    main()
