# Optional service and custom models

Native Hermes/OpenClaw plugins and local MCP do not require these settings.
Use this mode only when you explicitly want a separately running authenticated
HTTP service, its browser inspector, or independently configured models.

```sh
bigfeels-mem init
bigfeels-mem pair inspector --space owner
bigfeels-mem serve
```

The optional inspector is at `http://127.0.0.1:8765`. The token authenticates an
HTTP client; it is not a bigfeels account or a model-provider credential. The
browser keeps it only in memory. Each token has explicit space grants and
authorizes all memory operations in those spaces. Revoke one with
`bigfeels-mem revoke --token-env BIGFEELS_MEM_TOKEN`.

For MCP over this HTTP service, explicitly supply arguments
`["mcp", "--url", "http://127.0.0.1:8765"]` and the paired
`BIGFEELS_MEM_TOKEN`. Without `--url`, MCP uses direct local access.

Native integrations do not read this separate model configuration. For an
independent service, an optional OpenAI-compatible extraction/embedding provider
can be configured as follows:

```sh
bigfeels-mem configure --base-url https://api.openai.com/v1 \
  --api-key-env OPENAI_API_KEY --extraction-model YOUR_CHAT_MODEL \
  --embedding-model YOUR_EMBEDDING_MODEL --allow-remote
```

Inject the key through your service manager's secret environment. Only the
environment variable name is stored in `config.json`; this advanced mode does
not provide its own credential vault or subscription login. Prefer the native
host integration to inherit its credential management.

Remote processing sends redacted evidence for extraction, memory content for
embeddings, and queries for query embeddings. Redaction is best effort; it cannot
recognize arbitrary private information. A loopback compatible provider can be
used without `--allow-remote`. Restart this separate service after configuration
changes. Extraction needs JSON-object responses; embeddings need an actual
embedding model. A chat subscription is not an embedding API credential.

Without a provider the service still supports explicit saves, corrections,
forgetting, evidence inspection, keyword retrieval, and export. Captured events
remain queued until a processor with access to their spaces handles them.
