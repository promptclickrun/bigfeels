import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import { createServer } from "node:http";
import { afterEach, test } from "node:test";
import os from "node:os";
import { spawn } from "node:child_process";

import { createOpenClawAdapter, registerOpenClawSurface } from "./adapter.js";
import { toolDefinitions } from "./tools.js";


const servers = [];
const dataDirectories = [];
const nativePython = process.env.BIGFEELS_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))));
  for (const directory of dataDirectories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

function nativeDataDirectory() {
  const directory = fs.mkdtempSync(`${os.tmpdir()}/bigfeels-openclaw-`);
  dataDirectories.push(directory);
  return directory;
}

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
    captureRoles: ["user", "assistant", "tool"],
    autoRecall: true,
    ...overrides,
  };
}

function isSubagentSessionKey(value) {
  return typeof value === "string" && value.includes(":subagent:");
}

function fakeChild(onRequest) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.killSignals = [];
  child.stdin = {
    writes: [],
    endCalls: 0,
    destroyCalls: 0,
    write(value) {
      this.writes.push(value);
      onRequest(value, child);
      return true;
    },
    end() {
      this.endCalls += 1;
    },
    destroy() {
      this.destroyCalls += 1;
    },
  };
  child.kill = (signal) => {
    child.killSignals.push(signal);
    return true;
  };
  return child;
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
    "bigfeels_forget_preview",
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

test("native Python child storage works without network auth", async () => {
  const environments = [];
  const adapter = createOpenClawAdapter({
    config: { dataDir: nativeDataDirectory(), processTimeoutMs: 5000, pythonPath: nativePython },
    fetchImpl: async () => {
      throw new Error("native mode must not call fetch");
    },
    spawnImpl: (command, args, options) => {
      environments.push({ command, args, env: options.env });
      return spawn(command, args, options);
    },
  });

  assert.equal(adapter.mode, "native");
  assert.deepEqual(adapter.spaces, ["owner"]);
  assert.equal(adapter.writeSpace, "owner");
  const saved = await adapter.post("remember", {
    space: "owner",
    content: "Native child storage works",
    kind: "fact",
  });
  const recalled = await adapter.post("context", {
    query: "Native child storage",
    spaces: ["owner"],
    budget: 800,
  });

  assert.equal(saved.content, "Native child storage works");
  assert.equal(recalled.memories[0].content, "Native child storage works");
  assert.ok(environments.length >= 2);
  assert.ok(environments.every(({ command }) => command === nativePython));
  assert.ok(environments.every(({ env }) => env.PYTHONUTF8 === "1"));
  assert.ok(environments.every(({ env }) => !Object.keys(env).some((key) => /API|TOKEN|SECRET|KEY/i.test(key))));
});

test("native extraction uses the host model callback and inherits its model boundary", async () => {
  const completions = [];
  const adapter = createOpenClawAdapter({
    config: { dataDir: nativeDataDirectory(), processTimeoutMs: 5000, pythonPath: nativePython, captureRoles: ["user"], autoExtract: true },
    complete: async (request) => {
      completions.push(request);
      return {
        text: JSON.stringify({
          memories: [{
            content: "I prefer native memory",
            quote: "I prefer native memory",
            basis: "direct",
            kind: "preference",
          }],
        }),
      };
    },
  });
  const ctx = { runId: "native-run", agentId: "main", sessionKey: "agent:main:main" };

  await adapter.agentEnd({
    runId: ctx.runId,
    success: true,
    messages: [
      { role: "user", content: "I prefer native memory" },
      { role: "assistant", content: "I will remember that." },
    ],
  }, ctx);
  await adapter.processPending(ctx);

  assert.ok(completions.length >= 1);
  assert.ok(completions.every((request) => request.maxTokens === 1200));
  assert.ok(completions.every((request) => request.signal instanceof AbortSignal));
  assert.ok(completions.every((request) => request.agentId === "main"));
  assert.ok(completions.every((request) => !Object.hasOwn(request, "model")));
  assert.ok(completions.every((request) => request.messages.some((message) => message.role === "system")));
  assert.doesNotMatch(JSON.stringify(completions), /OPENAI_API_KEY|Bearer\s+private|private-token/i);
});

test("native extraction does not call the model for background or nonprimary runs", async () => {
  let completions = 0;
  const adapter = createOpenClawAdapter({
    config: { dataDir: nativeDataDirectory(), processTimeoutMs: 5000, captureRoles: ["user"], autoExtract: true },
    complete: async () => {
      completions += 1;
      return { text: '{"memories":[]}' };
    },
  });
  const cases = [
    { runId: "system", agentId: "main", sessionKey: "agent:main:main", trigger: "system" },
    { runId: "secondary", agentId: "secondary", sessionKey: "agent:secondary:main" },
  ];
  for (const ctx of cases) {
    await adapter.agentEnd({
      runId: ctx.runId,
      success: true,
      messages: [{ role: "user", content: "background evidence" }, { role: "assistant", content: "ignored" }],
    }, ctx);
  }
  assert.equal(completions, 0);
});

test("native model timeout aborts completion, closes stdin, and terminates child", async () => {
  let child;
  let completionSignal;
  const adapter = createOpenClawAdapter({
    config: { processTimeoutMs: 25, captureRoles: ["user"], autoExtract: true },
    spawnImpl: (_command, _args, _options) => {
      child = fakeChild((value, currentChild) => {
        const request = JSON.parse(value);
        if (request.operation === "process") {
          setImmediate(() => currentChild.stdout.emit("data", `${JSON.stringify({
            id: "extract-timeout",
            method: "extract",
            messages: [{ role: "user", content: "slow" }],
          })}\n`));
        }
      });
      return child;
    },
    complete: ({ signal }) => {
      completionSignal = signal;
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("completion aborted")), { once: true });
      });
    },
  });

  await assert.rejects(
    adapter.post("process", { limit: 1 }, { runId: "timeout", agentId: "main", sessionKey: "agent:main:main" }),
    /timed out/,
  );
  assert.ok(completionSignal?.aborted);
  assert.deepEqual(child.killSignals, ["SIGKILL"]);
  assert.equal(child.stdin.endCalls, 1);
  assert.equal(child.stdin.destroyCalls, 1);
});

test("native child protocol preserves UTF-8 across fragmented stdout chunks", async () => {
  let child;
  const adapter = createOpenClawAdapter({
    config: { processTimeoutMs: 500 },
    spawnImpl: (_command, _args, _options) => {
      child = fakeChild((value, currentChild) => {
        const request = JSON.parse(value);
        if (request.operation !== "context") return;
        const response = Buffer.from(JSON.stringify({
          id: request.id,
          result: { status: "ok", memories: [{ content: "café" }] },
        }) + "\n", "utf8");
        const split = response.indexOf(Buffer.from("é", "utf8")) + 1;
        setImmediate(() => {
          currentChild.stdout.emit("data", response.subarray(0, split));
          currentChild.stdout.emit("data", response.subarray(split));
          currentChild.emit("close", 0);
        });
      });
      return child;
    },
  });

  const response = await adapter.post("context", { query: "cafe", spaces: ["owner"], budget: 800 }, {
    runId: "fragmented",
    agentId: "main",
    sessionKey: "agent:main:main",
  });
  assert.equal(response.memories[0].content, "café");
});

test("native malformed protocol rejects and closes the child input", async () => {
  let child;
  const adapter = createOpenClawAdapter({
    config: { processTimeoutMs: 500 },
    spawnImpl: (_command, _args, _options) => {
      child = fakeChild((value, currentChild) => {
        const request = JSON.parse(value);
        if (request.operation === "context") setImmediate(() => currentChild.stdout.emit("data", "{malformed\n"));
      });
      return child;
    },
  });

  await assert.rejects(
    adapter.post("context", { query: "malformed", spaces: ["owner"], budget: 800 }, {
      runId: "malformed",
      agentId: "main",
      sessionKey: "agent:main:main",
    }),
    /invalid JSON/,
  );
  assert.deepEqual(child.killSignals, ["SIGKILL"]);
  assert.equal(child.stdin.endCalls, 1);
  assert.equal(child.stdin.destroyCalls, 1);
});
