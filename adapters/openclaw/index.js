import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { isSubagentSessionKey } from "openclaw/plugin-sdk/routing";

import { createOpenClawAdapter, registerOpenClawSurface } from "./adapter.js";
import { toolDefinitions } from "./tools.js";


export default definePluginEntry({
  id: "bigfeels-mem",
  name: "bigfeels memory",
  description: "Native evidence-based recall and governed capture through the bigfeels local core.",
  kind: "memory",
  register(api) {
    const runtimeComplete = api.runtime?.llm?.complete;
    const allowAgentIdOverride = api.config?.plugins?.entries?.["bigfeels-mem"]?.llm?.allowAgentIdOverride === true;
    const adapter = createOpenClawAdapter({
      config: api.pluginConfig ?? {},
      fetchImpl: globalThis.fetch,
      complete: typeof runtimeComplete === "function" ? async ({ agentId, ...request }) => {
        // The public runtime resolves the default agent when no override is
        // supplied. A non-default primary agent needs the host's explicit
        // plugin LLM capability; otherwise leave extraction queued.
        if (agentId && agentId !== "main" && !allowAgentIdOverride) {
          throw new Error("bigfeels extraction requires explicit host agent binding");
        }
        return runtimeComplete.call(api.runtime.llm, {
          ...request,
          ...(agentId && agentId !== "main" ? { agentId } : {}),
        });
      } : undefined,
      isSubagentSessionKey,
      logger: api.logger,
    });
    registerOpenClawSurface({ api, adapter, toolDefinitions });
  },
});
