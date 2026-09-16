import test from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { createOpenClawAdapter } from '../adapters/openclaw/adapter.js';

function fixture({ failFirst = false } = {}) {
  const state = { captured: 0, pending: 0, done: 0, calls: 0, retryAt: null };
  const ctx = { agentId: 'main', sessionKey: 'review-session', runId: 'review-run', trigger: 'user' };
  const adapter = createOpenClawAdapter({
    config: {},
    transport: async (operation) => {
      if (operation === 'context') return { status: 'ok', memories: [] };
      if (operation === 'observe') {
        state.captured += 1;
        state.pending += 1;
        return { id: `ev-${state.captured}`, status: 'queued' };
      }
      if (operation === 'process') {
        state.calls += 1;
        if (failFirst && state.calls === 1) {
          state.retryAt = Date.now() / 1000 + 0.05;
          return { processed: 0 };
        }
        if (state.retryAt && Date.now() / 1000 < state.retryAt) return { processed: 0 };
        if (!state.pending) return { processed: 0 };
        state.pending -= 1;
        state.done += 1;
        state.retryAt = null;
        return { processed: 1 };
      }
      if (operation === 'status') return {
        queue: { pending: state.pending, processing: 0, done: state.done, failed: 0 },
        processing: { oldest_pending_at: '2026-01-01T00:00:00Z', next_retry_at: state.retryAt },
      };
      throw new Error(`Unexpected operation ${operation}`);
    },
  });
  return { adapter, ctx, state };
}

async function until(predicate, timeoutMs = 2500) {
  const end = Date.now() + timeoutMs;
  while (!predicate() && Date.now() < end) await delay(10);
}

test('automatic extraction continues beyond eight jobs without a second user turn', async (t) => {
  const { adapter, ctx, state } = fixture();
  t.after(() => adapter.sessionEnd({}, ctx));
  await adapter.beforePromptBuild({ prompt: 'Summarize the checks.' }, ctx);
  const messages = [{ role: 'user', content: 'Summarize the checks.' },
    ...Array.from({ length: 10 }, (_, i) => ({ role: 'tool', tool_call_id: `tool-${i}`, name: 'check', content: `Check ${i} passed.` })),
    { role: 'assistant', content: 'The checks finished.' }];
  await adapter.agentEnd({ runId: ctx.runId, success: true, messages }, ctx);
  assert.equal(state.captured, 12);
  await until(() => state.pending === 0);
  assert.equal(state.pending, 0, 'Idle conversation left a tail of captured but unprocessed events');
  assert.equal(state.done, state.captured);
});

test('pending retry resumes without another user turn', async (t) => {
  const { adapter, ctx, state } = fixture({ failFirst: true });
  t.after(() => adapter.sessionEnd({}, ctx));
  await adapter.beforePromptBuild({ prompt: 'I prefer plain text.' }, ctx);
  await adapter.agentEnd({ runId: ctx.runId, success: true, messages: [
    { role: 'user', content: 'I prefer plain text.' },
    { role: 'assistant', content: 'Understood.' },
  ] }, ctx);
  await until(() => state.pending === 0);
  assert.equal(state.pending, 0, 'Retryable extraction waited for another conversation turn');
  assert.equal(state.done, state.captured);
});

for (const ending of ['session', 'gateway']) {
  test(`${ending} end prevents capture writes after an in-flight observation`, async () => {
    let releaseFirst;
    let firstStartedResolve;
    const firstStarted = new Promise((resolve) => { firstStartedResolve = resolve; });
    const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
    const observations = [];
    const ctx = { agentId: 'main', sessionKey: `ending-${ending}`, runId: `run-${ending}`, trigger: 'user' };
    const adapter = createOpenClawAdapter({
      config: {},
      transport: async (operation, payload) => {
        if (operation === 'context') return { status: 'ok', memories: [] };
        if (operation === 'observe') {
          observations.push(payload.source_event_id);
          if (observations.length === 1) {
            firstStartedResolve();
            await firstGate;
          }
          return { id: `ev-${observations.length}`, status: 'queued' };
        }
        if (operation === 'process') return { processed: 0 };
        if (operation === 'status') return { queue: { pending: 0, processing: 0 }, processing: {} };
        throw new Error(`Unexpected operation ${operation}`);
      },
    });
    await adapter.beforePromptBuild({ prompt: 'Record this turn.' }, ctx);
    const capture = adapter.agentEnd({ runId: ctx.runId, success: true, messages: [
      { role: 'user', content: 'Record this turn.' },
      { role: 'tool', tool_call_id: 'tool-1', content: 'Tool result.' },
      { role: 'assistant', content: 'Recorded.' },
    ] }, ctx);
    await firstStarted;
    if (ending === 'session') adapter.sessionEnd({}, ctx);
    else adapter.shutdown();
    releaseFirst();
    await capture;
    assert.equal(observations.length, 1, 'capture resumed with new writes after lifecycle end');
  });
}
