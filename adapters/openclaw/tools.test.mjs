import assert from "node:assert/strict";
import { test } from "node:test";

import { toolDefinitions } from "./tools.js";


test("definitions expose the seven bounded native OpenClaw tools", () => {
  const tools = toolDefinitions({
    post: async () => ({}),
    spaces: ["owner:gordie"],
    writeSpace: "owner:gordie",
  });

  assert.deepEqual(tools.map((tool) => tool.name), [
    "bigfeels_search",
    "bigfeels_inspect",
    "bigfeels_remember",
    "bigfeels_correct",
    "bigfeels_forget_preview",
    "bigfeels_forget",
    "bigfeels_status",
  ]);
  assert.ok(tools.every((tool) => typeof tool.label === "string" && tool.label.length > 0));
  assert.ok(tools.every((tool) => typeof tool.description === "string" && tool.description.length > 0));
  assert.ok(tools.every((tool) => typeof tool.execute === "function"));
  const schemas = Object.fromEntries(tools.map((tool) => [tool.name, tool.parameters]));
  assert.deepEqual(schemas.bigfeels_search.required, ["query"]);
  assert.ok("include_inactive" in schemas.bigfeels_search.properties);
  assert.deepEqual(schemas.bigfeels_remember.required, ["content"]);
  assert.deepEqual(schemas.bigfeels_correct.required, ["id", "revision", "content"]);
  assert.deepEqual(schemas.bigfeels_forget.required, ["id", "plan_token"]);
  assert.deepEqual(schemas.bigfeels_status.properties, {});
  assert.ok(tools.every((tool) => tool.parameters.additionalProperties === false));
});

test("execute applies configured search and remember defaults and returns native content", async () => {
  const calls = [];
  const post = async (operation, payload) => {
    calls.push({ operation, payload });
    return { operation, payload };
  };
  const tools = toolDefinitions({
    post,
    spaces: ["owner:gordie", "project:bigfeels"],
    writeSpace: "project:bigfeels",
  });
  const byName = Object.fromEntries(tools.map((tool) => [tool.name, tool]));
  const searchParams = { query: "release gate", budget: 900 };
  const rememberParams = { content: "Ship after the gate", kind: "procedure" };

  const search = await byName.bigfeels_search.execute("call-search", searchParams);
  const remember = await byName.bigfeels_remember.execute("call-remember", rememberParams);

  assert.deepEqual(searchParams, { query: "release gate", budget: 900 });
  assert.deepEqual(rememberParams, { content: "Ship after the gate", kind: "procedure" });
  assert.deepEqual(calls, [
    {
      operation: "search",
      payload: {
        query: "release gate",
        budget: 900,
        spaces: ["owner:gordie", "project:bigfeels"],
      },
    },
    {
      operation: "remember",
      payload: {
        content: "Ship after the gate",
        kind: "procedure",
        space: "project:bigfeels",
      },
    },
  ]);
  assert.deepEqual(search.details, calls[0].payload && { operation: "search", payload: calls[0].payload });
  assert.deepEqual(JSON.parse(search.content[0].text), search.details);
  assert.deepEqual(remember.details, calls[1].payload && { operation: "remember", payload: calls[1].payload });
  assert.deepEqual(JSON.parse(remember.content[0].text), remember.details);
});

test("requested spaces outside configuration return a safe result without posting", async () => {
  const calls = [];
  const tools = toolDefinitions({
    post: async (...args) => {
      calls.push(args);
      return { unexpected: true };
    },
    spaces: ["owner:gordie", "project:bigfeels"],
    writeSpace: "owner:gordie",
  });
  const byName = Object.fromEntries(tools.map((tool) => [tool.name, tool]));

  const search = await byName.bigfeels_search.execute("denied-search", {
    query: "private",
    spaces: ["other:private"],
  });
  const remember = await byName.bigfeels_remember.execute("denied-remember", {
    content: "private",
    space: "other:private",
  });

  const expected = { error: { message: "Requested memory space is not allowed." } };
  assert.deepEqual(search.details, expected);
  assert.deepEqual(remember.details, expected);
  assert.deepEqual(calls, []);
});

test("each definition posts its matching service operation", async () => {
  const calls = [];
  const tools = toolDefinitions({
    post: async (operation, payload) => {
      calls.push({ operation, payload });
      return { ok: operation };
    },
    spaces: ["owner:gordie"],
    writeSpace: "owner:gordie",
  });
  const byName = Object.fromEntries(tools.map((tool) => [tool.name, tool]));
  const cases = [
    ["bigfeels_inspect", { id: "memory-1" }, "inspect"],
    ["bigfeels_correct", { id: "memory-1", revision: 2, content: "Updated" }, "correct"],
    ["bigfeels_forget", { id: "memory-1", plan_token: "current-plan" }, "forget"],
    ["bigfeels_status", {}, "status"],
  ];
  for (const [name, params, operation] of cases) {
    const result = await byName[name].execute(`call-${operation}`, params);
    assert.deepEqual(result.details, { ok: operation });
  }
  assert.deepEqual(calls.map(({ operation }) => operation), cases.map((entry) => entry[2]));
});

test("service failures do not echo arguments or credentials", async () => {
  const secret = "private-token-and-argument";
  const [inspect] = toolDefinitions({
    post: async () => {
      throw new Error(`request failed with ${secret}`);
    },
    spaces: ["owner:gordie"],
    writeSpace: "owner:gordie",
  }).filter((tool) => tool.name === "bigfeels_inspect");

  const result = await inspect.execute("call-secret", { id: secret });

  assert.deepEqual(result.details, { error: { message: "Memory service request failed." } });
  assert.deepEqual(JSON.parse(result.content[0].text), result.details);
  assert.doesNotMatch(JSON.stringify(result), new RegExp(secret));
});
