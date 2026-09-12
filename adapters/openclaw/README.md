# OpenClaw adapter

This package is a native OpenClaw memory plugin. It registers the documented
`before_prompt_build`, `agent_end`, and `session_end` hooks and explicit memory
tools through the Plugin SDK. It uses Node's built-in `fetch`; there are no
runtime dependencies besides OpenClaw.

Install this local package with OpenClaw's plugin installer:

```sh
bigfeels-mem init
bigfeels-mem space project:app
bigfeels-mem pair openclaw --space owner --space project:app
bigfeels-mem serve

openclaw plugins install /absolute/path/to/bigfeels-mem/adapters/openclaw
```

Add a normal plugin entry using a bearer token created by `bigfeels-mem pair`:

```json
{
  "plugins": {
    "entries": {
      "bigfeels-mem": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true
        },
        "config": {
          "url": "http://127.0.0.1:8765",
          "token": "${BIGFEELS_MEM_TOKEN}",
          "ownerSpace": "owner",
          "projectSpaces": ["project:app"],
          "writeSpace": "project:app",
          "primaryAgentId": "main",
          "budget": 800,
          "timeoutMs": 750,
          "autoCapture": true,
          "autoRecall": true
        }
      }
    }
  }
}
```

OpenClaw requires the explicit `allowConversationAccess` gate for installed
plugins that observe `agent_end`. The server-side token scopes must cover the
owner space, each listed project space, and the write space. The write space
defaults to the owner space. Plain HTTP endpoints must be loopback.

The adapter accepts only the configured primary agent, requires OpenClaw's
stable run and session identities, rejects subagent session keys through the
official routing helper, and rejects scheduled/background jobs. Hook and HTTP
timeouts are bounded and fail open so memory outages do not block a turn. It
registers `bigfeels_search`, `bigfeels_inspect`, `bigfeels_remember`,
`bigfeels_correct`, `bigfeels_forget`, and `bigfeels_status` as native tools;
the same configured space allowlist is enforced before each tool request.

Run the adapter tests directly with:

```sh
npm test --prefix adapters/openclaw
```
