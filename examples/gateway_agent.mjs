// Node 18+, no MCP SDK and no local tool registry in the agent.
// node examples/gateway_agent.mjs http://127.0.0.1:8010/api INSTANCE_ID
import { randomUUID } from 'node:crypto';
const [, , base = 'http://127.0.0.1:8010/api', sid] = process.argv;
if (!sid) throw new Error('Pass a context instance ID with central tool sources selected');
async function post(path, body) {
  const response = await fetch(base.replace(/\/$/, '') + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  return response.json();
}
const path = `/sessions/${encodeURIComponent(sid)}`;
const turnId = randomUUID();
await post(path + '/ingest', { type: 'message.append', update_id: randomUUID(), turn_id: turnId,
  payload: { role: 'user', content: 'Calculate using the available arithmetic tool' } });
let finished = false;
for (let step = 0; step < 10; step++) {
  const prepared = await post(path + '/prepare', {});
  if (!prepared.ready || prepared.execution_mode !== 'gateway') throw new Error(JSON.stringify(prepared.issues));
  // Replace this deterministic model stand-in with a provider SDK call.
  if (step > 0) {
    const result = [...prepared.compiled.model_input.messages].reverse().find(m => m.role === 'tool');
    await post(path + '/ingest', { type: 'message.append', update_id: randomUUID(), turn_id: turnId,
      payload: { role: 'assistant', content: result.content } });
    console.log(result.content);
    finished = true;
    break;
  }
  const tool = prepared.compiled.model_input.tools.find(t => /\/(add|multiply)$/.test(t.tool_ref));
  if (!tool) throw new Error('This demo requires an add or multiply tool');
  const callId = randomUUID();
  const assistant = await post(path + '/ingest', { type: 'message.append', update_id: randomUUID(), turn_id: turnId,
    payload: { role: 'assistant', content: '', tool_call_ids: [callId] } });
  await post(path + '/tools/call', { preparation_id: prepared.preparation_id, call_id: callId, name: tool.name,
    arguments: { a: 6, b: 7 }, assistant_seq: assistant.update.seq, turn_id: turnId });
}
if (!finished) throw new Error('Agent step limit reached');
