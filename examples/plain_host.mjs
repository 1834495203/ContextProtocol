// Independent JavaScript HTTP host. Node 18+, no SDK or npm dependencies.
// node examples/plain_host.mjs http://127.0.0.1:8010/api SESSION_ID
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export async function continueContext(baseUrl, sessionId) {
  async function request(path, body) {
    const response = await fetch(`${baseUrl.replace(/\/$/, '')}${path}`, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    return response.json();
  }
  const discovery = await request('/protocol');
  if (!discovery.host_protocol_versions.includes('0.1')) throw new Error('Incompatible host protocol');
  const path = `/sessions/${encodeURIComponent(sessionId)}`;
  const bindingId = randomUUID();
  await request(`${path}/ingest`, {
    protocol_version: '0.1', update_id: randomUUID(), source: 'plain-javascript',
    type: 'host.open', payload: { binding_id: bindingId, host_id: 'javascript' },
  });
  try {
    // This host intentionally executes no tools; cached definitions are not capabilities.
    await request(`${path}/ingest`, {
      protocol_version: '0.1', update_id: randomUUID(), source: 'plain-javascript',
      type: 'provider.sync', payload: { binding_id: bindingId, provider_id: 'local', kind: 'local', status: 'ready', tools: [] },
    });
    const turnId = randomUUID();
    await request(`${path}/ingest`, {
      protocol_version: '0.1', update_id: randomUUID(), source: 'plain-javascript', turn_id: turnId,
      type: 'message.append', payload: { role: 'user', content: 'Read the saved context in a new host' },
    });
    const prepared = await request(`${path}/prepare`, {
      binding_id: bindingId,
      supported_versions: ['0.1'],
      capabilities: { roles: ['system', 'user', 'assistant', 'tool'], tool_calls: true },
    });
    if (!prepared.ready) throw new Error(JSON.stringify(prepared.issues));
    // A deterministic model stand-in: read compiled input, without executing tools.
    const content = prepared.compiled.model_input.messages.map(m => m.content).join('\n');
    await request(`${path}/ingest`, {
      protocol_version: '0.1', update_id: randomUUID(), source: 'plain-javascript', turn_id: turnId,
      type: 'message.append', payload: { role: 'assistant', content: 'Context received by JavaScript host' },
    });
    return content;
  } finally {
    await request(`${path}/ingest`, {
      protocol_version: '0.1', update_id: randomUUID(), source: 'plain-javascript',
      type: 'host.close', payload: { binding_id: bindingId },
    });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [, , url = 'http://127.0.0.1:8010/api', sid] = process.argv;
  if (!sid) throw new Error('Pass a context session ID as the second argument');
  console.log(await continueContext(url, sid));
}
