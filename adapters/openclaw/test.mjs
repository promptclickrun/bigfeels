import assert from "node:assert/strict";
import { createServer } from "node:http";
import { afterEach, test } from "node:test";

import { createOpenClawAdapter, registerOpenClawSurface } from "./adapter.js";
import { toolDefinitions } from "./tools.js";


const servers = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))));
});

async function startMemoryService({ contextForQuery = () => ({ memories: [], tokens: 0, status: "ok", trace: {} }), delayMs = 0 } = {}) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    requests.push({
      path: request.url,
      authorization: request.headers.authorization,
      contentType: request.headers["content-type"],
      body,
    });
    if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
    const payload = request.url === "/v1/context"
      ? contextForQuery(body.query)
      : { id: "evidence", status: "accepted" };
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(payload));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  servers.push(server);
  const address = server.address();
  return { requests, url: `http://127.0.0.1:${address.port}` };
}

function config(url, overrides = {}) {
  return {
    url,
    token: "openclaw-top-secret",
    ownerSpace: "owner:gordie",
    projectSpaces: ["project:bigfeels"],
    writeSpace: "project:bigfeels",
    primaryAgentId: "main",
    budget: 180,
    timeoutMs: 250,
    autoCapture: true,
    autoRecall: true,
    ...overrides,
  };
}

function isSubagentSessionKey(value) {
  return typeof value === "string" && value.includes(":subagent:");
}

test("native registration exposes lifecycle hooks and all declared tools", () => {
  const hooks = [];
  const tools = [];
  const api = {
    pluginConfig: { timeoutMs: 750 },
    on(name, handler, options) {
      hooks.push({ name, handler, options });
    },
    registerTool(tool, options) {
      tools.push({ tool, options });
    },
  };
  const adapter = {
    beforePromptBuild() {},
    agentEnd() {},
    sessionEnd() {},
    post: async () => ({}),
    spaces: ["owner"],
    writeSpace: "owner",
  };

  registerOpenClawSurface({ api, adapter, toolDefinitions });

  assert.deepEqual(hooks.map(({ name }) => name), ["before_prompt_build", "agent_end", "session_end"]);
  assert.equal(hooks[0].options.timeoutMs, 1250);
  assert.equal(hooks[1].options.timeoutMs, 1250);
  assert.equal(hooks[2].options, undefined);
  assert.deepEqual(tools.map(({ tool }) => tool.name), [
    "bigfeels_search",
    "bigfeels_inspect",
    "bigfeels_remember",
    "bigfeels_correct",
    "bigfeels_forget",
    "bigfeels_status",
  ]);
  assert.ok(tools.every(({ tool, options }) => options.name === tool.name));
});

test("recall and capture preserve stable run identity, roles, scopes, and provenance", async () => {
  const memory = await startMemoryService({
    contextForQuery: () => ({
      memories: [{
        id: "memory-3",
        content: "Ship only after a real check.",
        kind: "procedure",
        basis: "direct",
        outcome: "attempted",
        evidence_ids: ["evidence-3"],
        valid_from: "2026-09-01T00:00:00Z",
        valid_until: null,
        status: "active",
        source_availability: "available",
        reason: "keyword match",
      }],
      tokens: 9,
      status: "ok",
      trace: {},
    }),
  });
  const warnings = [];
  const adapter = createOpenClawAdapter({
    config: config(memory.url),
    fetchImpl: fetch,
    isSubagentSessionKey,
    logger: { warn: (message) => warnings.push(message) },
  });
  const ctx = { runId: "run-42", agentId: "main", sessionKey: "agent:main:main" };

  const recall = await adapter.beforePromptBuild({ prompt: "What is the release rule?", messages: [] }, ctx);
  await adapter.agentEnd({
    runId: "run-42",
    success: true,
    messages: [
      { role: "user", content: "What is the release rule?" },
      {
        role: "assistant",
        content: "",
        tool_calls: [
          { id: "memory-tool", function: { name: "bigfeels_search" } },
          { id: "tool-8", function: { name: "shell" } },
        ],
      },
      { role: "tool", toolCallId: "memory-tool", content: '{"memories":[{"content":"old memory"}]}' },
      { role: "tool", toolName: "bigfeels_status", content: '{"status":"ok"}' },
      { role: "tool", toolCallId: "tool-8", content: "The check was attempted, not verified." },
      { role: "assistant", content: [{ type: "text", text: "I attempted the check; it is not verified yet." }] },
    ],
  }, ctx);
  // Replayed host completion reaches the service with identical source keys.
  await adapter.agentEnd({
    runId: "run-42",
    success: true,
    messages: [
      { role: "user", content: "What is the release rule?" },
      {
        role: "assistant",
        content: "",
        tool_calls: [
          { id: "memory-tool", function: { name: "bigfeels_search" } },
          { id: "tool-8", function: { name: "shell" } },
        ],
      },
      { role: "tool", toolCallId: "memory-tool", content: '{"memories":[{"content":"old memory"}]}' },
      { role: "tool", toolCallId: "tool-8", content: "The check was attempted, not verified." },
      { role: "assistant", content: "I attempted the check; it is not verified yet." },
    ],
  }, ctx);

  assert.match(recall.prependContext, /Ship only after a real check\./);
  assert.match(recall.prependContext, /evidence=evidence-3/);
  assert.match(recall.prependContext, /valid_from=2026-09-01T00:00:00Z/);
  assert.match(recall.prependContext, /source_availability=available/);
  assert.doesNotMatch(recall.prependContext, /openclaw-top-secret/);
  assert.equal(warnings.length, 0);
  assert.deepEqual(memory.requests[0], {
    path: "/v1/context",
    authorization: "Bearer openclaw-top-secret",
    contentType: "application/json",
    body: {
      query: "What is the release rule?",
      spaces: ["owner:gordie", "project:bigfeels"],
      budget: 180,
    },
  });
  const observations = memory.requests.slice(1).map((entry) => entry.body);
  assert.deepEqual(observations.map((item) => item.speaker), ["user", "tool", "assistant", "user", "tool", "assistant"]);
  assert.deepEqual(observations.map((item) => item.source_event_id), [
    "openclaw:agent:main:main:run-42:user",
    "openclaw:agent:main:main:run-42:tool:tool-8",
    "openclaw:agent:main:main:run-42:assistant",
    "openclaw:agent:main:main:run-42:user",
    "openclaw:agent:main:main:run-42:tool:tool-8",
    "openclaw:agent:main:main:run-42:assistant",
  ]);
  assert.equal(observations[1].content, "The check was attempted, not verified.");
  assert.equal("outcome" in observations[1], false);
  assert.equal("origin_ids" in observations[2], false);
  assert.equal("outcome" in observations[2], false);
  assert.equal(observations[2].content, "I attempted the check; it is not verified yet.");
  assert.ok(observations.every((item) => item.space === "project:bigfeels"));
});

test("prefetch provenance stays isolated between concurrent runs in one session", async () => {
  const memory = await startMemoryService({
    contextForQuery: (query) => ({
      memories: [{
        id: `memory-${query}`,
        content: `memory for ${query}`,
        evidence_ids: [`evidence-${query}`],
        status: "active",
      }],
      tokens: 4,
      status: "ok",
      trace: {},
    }),
  });
  const adapter = createOpenClawAdapter({
    config: config(memory.url),
    fetchImpl: fetch,
    isSubagentSessionKey,
    logger: { warn() {} },
  });
  const base = { agentId: "main", sessionKey: "agent:main:main" };
  await Promise.all([
    adapter.beforePromptBuild({ prompt: "alpha", messages: [] }, { ...base, runId: "run-a" }),
    adapter.beforePromptBuild({ prompt: "beta", messages: [] }, { ...base, runId: "run-b" }),
  ]);
  await adapter.agentEnd(
    { runId: "run-a", success: true, messages: [{ role: "assistant", content: "memory for alpha" }] },
    { ...base, runId: "run-a" },
  );
  await adapter.agentEnd(
    { runId: "run-b", success: true, messages: [{ role: "assistant", content: "memory for beta" }] },
    { ...base, runId: "run-b" },
  );

  const assistantObservations = memory.requests
    .filter((entry) => entry.path === "/v1/observe" && entry.body.speaker === "assistant")
    .map((entry) => entry.body);
  assert.deepEqual(assistantObservations.map((item) => item.origin_ids), [
    ["evidence-alpha"],
    ["evidence-beta"],
  ]);
});

test("nonprimary, subagent, scheduled, failed, and identityless runs do not capture", async () => {
  const memory = await startMemoryService();
  const adapter = createOpenClawAdapter({
    config: config(memory.url),
    fetchImpl: fetch,
    isSubagentSessionKey,
    logger: { warn() {} },
  });
  const cases = [
    { ctx: { runId: "other", agentId: "secondary", sessionKey: "agent:secondary:main" }, event: { runId: "other", success: true, messages: [] } },
    { ctx: { runId: "child", agentId: "main", sessionKey: "agent:main:subagent:child" }, event: { runId: "child", success: true, messages: [] } },
    { ctx: { runId: "cron", agentId: "main", sessionKey: "agent:main:main", jobId: "job-1" }, event: { runId: "cron", success: true, messages: [] } },
    { ctx: { agentId: "main", sessionKey: "agent:main:main" }, event: { success: true, messages: [{ role: "assistant", content: "no stable run id" }] } },
    { ctx: { runId: "failed", agentId: "main", sessionKey: "agent:main:main" }, event: { runId: "failed", success: false, messages: [{ role: "assistant", content: "failed" }] } },
  ];
  for (const { ctx, event } of cases) {
    assert.equal(await adapter.beforePromptBuild({ prompt: "ignore", messages: [] }, ctx), undefined);
    await adapter.agentEnd(event, ctx);
  }
  assert.equal(memory.requests.filter((request) => request.path === "/v1/observe").length, 0);
});

test("fetch timeout is fail-open and log output excludes credentials", async () => {
  const memory = await startMemoryService({ delayMs: 100 });
  const warnings = [];
  const adapter = createOpenClawAdapter({
    config: config(memory.url, { timeoutMs: 10 }),
    fetchImpl: fetch,
    isSubagentSessionKey,
    logger: { warn: (message) => warnings.push(message) },
  });
  const result = await adapter.beforePromptBuild(
    { prompt: "slow", messages: [] },
    { runId: "slow-run", agentId: "main", sessionKey: "agent:main:main" },
  );

  assert.equal(result, undefined);
  assert.match(warnings.join("\n"), /unavailable/);
  assert.doesNotMatch(warnings.join("\n"), /openclaw-top-secret/);
});

test("plain HTTP service must be loopback", () => {
  assert.throws(
    () => createOpenClawAdapter({
      config: config("http://memory.example.test"),
      fetchImpl: fetch,
      isSubagentSessionKey,
      logger: { warn() {} },
    }),
    /loopback/,
  );
});
