# Standalone HTTP, UI, and custom models

The Python CLI and direct-local MCP are the simplest host-neutral interfaces. Use the standalone service when you want an authenticated loopback API, browser workspace, or a continuously running extraction worker.

## Start the service

```sh
bigfeels-mem init
bigfeels-mem pair inspector --space owner
bigfeels-mem serve
```

The API and UI listen at `http://127.0.0.1:8765` by default. The paired token is an HTTP credential scoped to named spaces, not a bigfeels account or model credential. The UI keeps it only in page memory.

The browser workspace supports explicit save, browse/search, inspect, correction, processing diagnostics/manual bounded processing, and deletion preview followed by plan-token confirmation.

For MCP over an existing service, use `mcp --url http://127.0.0.1:8765` with a scoped token environment. The bundled HTTP client supports the complete lifecycle, including deletion preview and bounded processing. Direct-local MCP avoids the service and token when all clients share the same OS trust boundary.

## Standalone provider configuration

Explicit memory operations work without a model. Only queued observations and optional embeddings need a provider.

```sh
bigfeels-mem configure --base-url https://api.openai.com/v1 \
  --api-key-env OPENAI_API_KEY --extraction-model YOUR_CHAT_MODEL \
  --embedding-model YOUR_EMBEDDING_MODEL --allow-remote
```

Inject the key through the process environment. `config.json` stores only the environment variable name, endpoint, models, timeout, and remote-transfer consent. A loopback-compatible provider does not require `--allow-remote`.

Remote processing sends redacted evidence for extraction, memory content for embeddings, and queries for query embeddings. Redaction is best effort. Provider redirects and HTTP proxies are disabled, requests are bounded, and diagnostics omit private source text and upstream error bodies.

Run `bigfeels-mem process --space SPACE --limit 8` for an explicit bounded batch or leave `serve` running for background processing. Always read the returned/current status. Queued, retrying, failed, and rejected work are distinct from completed learning.

## Security boundary

The service binds only to loopback, validates Host and browser Origin headers, and requires one bearer token per `/v1/*` operation. Static UI assets and `/health` are public but reveal no memory. Use OS permissions and disk encryption for storage. The HTTP token does not protect against another process that can already read the database as the same OS user.
