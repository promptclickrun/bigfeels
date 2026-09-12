import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { isSubagentSessionKey } from "openclaw/plugin-sdk/routing";

import { createOpenClawAdapter, registerOpenClawSurface } from "./adapter.js";
import { toolDefinitions } from "./tools.js";


export default definePluginEntry({
  id: "bigfeels-mem",
  name: "bigfeels memory",
  description: "Evidence-based recall and governed capture through the bigfeels local service.",
  kind: "memory",
  register(api) {
    const adapter = createOpenClawAdapter({
      config: api.pluginConfig,
      fetchImpl: globalThis.fetch,
      isSubagentSessionKey,
      logger: api.logger,
    });
    registerOpenClawSurface({ api, adapter, toolDefinitions });
  },
});
