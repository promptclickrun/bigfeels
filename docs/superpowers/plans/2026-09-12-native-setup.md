# Native, agent-installed bigfeels

> Historical implementation note. This plan records the native-adapter phase and is not current installation or product guidance. The host-neutral engine and interfaces in [../../../README.md](../../../README.md) and [../../../INSTALL.md](../../../INSTALL.md) are authoritative.

User correction: installation should be agent-driven from a repository link;
no required browser/pairing or separate model-provider setup. Inherit supported
host subscriptions and credential management through official runtime calls.

Implementation:
1. Root: local scoped Python client, scoped extraction worker, stdio bridge and
   direct local MCP default. Keep authenticated HTTP as an explicit alternative.
2. Hermes: native in-process default, official auxiliary model calls, no provider
   credential copies, root plugin discovery and no required setup fields.
3. OpenClaw: host-owned Python child transport and runtime.llm.complete for
   extraction; default owner space, no manual service/token/model configuration.
4. Root: agent-oriented README/install instructions, optional advanced setup docs,
   standalone/native loader and lifecycle verification; local commit only.

Local client contract: LocalClient(data_dir=None, spaces=('owner',), name='local',
extractor=None, auto_process=True), .store, .call(operation,payload), .post alias,
.process_pending(limit=8), .close(). Store.process_one gains optional spaces
filter so one host never submits an unlinked space to its model provider.

Bridge contract: Python bigfeels_mem.rpc.main(argv) takes --data-dir and repeated
--space. Reads one newline JSON request {id,operation,payload}. Normal result:
{id,result}. Error: {id,error:{message,status}}. Operation process handles up to
8 jobs. For extraction it emits {id,method:'extract',messages:[...]}; caller
replies {id,result:TEXT} or {id,error:true}. Only redacted evidence is sent.
No credentials travel across this pipe. Each child handles one operation.

New defaults are local storage + host text extraction + keyword retrieval.
Embeddings remain an optional advanced enhancement; subscription chat access
does not imply an embeddings endpoint. Do not silently use separate paid keys.
Do not edit a live host, log in, restart it, publish, or capture private data.
