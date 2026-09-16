import test from 'node:test';
import assert from 'node:assert/strict';
import { createOpenClawAdapter } from '../adapters/openclaw/adapter.js';

const ctx = { agentId: 'main', sessionKey: 'policy-session', runId: 'policy-run', trigger: 'user' };
const event = { runId: ctx.runId, success: true, messages: [
  { role: 'user', content: 'Private user turn' },
  { role: 'tool', name: 'm365', tool_call_id: 'one', content: 'Private document body' },
  { role: 'assistant', content: 'Private assistant summary' },
] };

for (const mode of ['native', 'http']) {
  test(`${mode} default captures nothing even when extraction is enabled alone`, async () => {
    for (const options of [{}, { autoExtract: true }]) {
      const calls = [];
      const adapter = createOpenClawAdapter({
        config: { ...(mode === 'http' ? { mode, url: 'http://127.0.0.1:8765', token: 'fixture' } : {}), ...options },
        transport: async (operation, payload) => {
          calls.push({ operation, payload });
          return { status: 'ok', memories: [] };
        },
        complete: async () => { assert.fail('Unapproved model submission'); },
      });
      try {
        await adapter.beforePromptBuild({ prompt: 'Private user turn' }, ctx);
        await adapter.agentEnd(event, ctx);
        await adapter.processPending(ctx);
        await adapter.post('process', {}, ctx);
        assert.deepEqual(calls.map(c => c.operation), ['context']);
        await adapter.post('remember', { space: 'owner', content: 'Approved distilled fact' }, ctx);
        assert.equal(calls.at(-1).operation, 'remember');
      } finally { adapter.shutdown(); }
    }
  });

  test(`${mode} user-only capture has stable finite TTL and no extraction jobs`, async () => {
    const observations = [];
    const adapter = createOpenClawAdapter({
      config: { ...(mode === 'http' ? { mode, url: 'http://127.0.0.1:8765', token: 'fixture' } : {}), captureRoles: ['user'] },
      transport: async (operation, payload) => {
        assert.notEqual(operation, 'process');
        if (operation === 'observe') observations.push(payload);
        return { status: 'ok', memories: [] };
      },
    });
    try {
      const started = Date.now();
      await adapter.beforePromptBuild({ prompt: 'Private user turn' }, ctx);
      await adapter.agentEnd(event, ctx);
      await adapter.beforePromptBuild({ prompt: 'Private user turn' }, ctx);
      await adapter.agentEnd(event, ctx);
      assert.deepEqual(observations.map(e => e.speaker), ['user', 'user']);
      assert.equal(observations[0].expires_at, observations[1].expires_at);
      assert.equal(observations[0].queue_extraction, false);
      const expiry = Date.parse(observations[0].expires_at);
      assert.ok(expiry >= started + 7 * 86400000 && expiry <= Date.now() + 7 * 86400000);
      assert.ok(observations.every(e => e.content === 'Private user turn'));
    } finally { adapter.shutdown(); }
  });
}

test('invalid policy fails closed rather than enabling capture', () => {
  for (const config of [
    { captureRoles: 'user' }, { captureRoles: ['document'] }, { captureRoles: ['user', 'user'] },
    { evidenceRetentionDays: 0 }, { evidenceRetentionDays: null }, { evidenceRetentionDays: Infinity },
    { autoExtract: 'false' }, { autoCapture: 'true' },
  ]) assert.throws(() => createOpenClawAdapter({ config }));
});
