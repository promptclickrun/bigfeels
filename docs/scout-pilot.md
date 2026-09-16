# Scout stdio MCP pilot

## Boundary

This pilot runs bigfeels as a **separate Python 3.11+ stdio process**. A Scout host that runs Python 3.9 starts that process; it does not import the bigfeels package into the host interpreter. The engine remains Python 3.11+.

This document defines only the portable MCP process boundary. It does not define or assume a Scout-specific registration API, native lifecycle hook, corpus reader, or deployment mechanism. Connect the launch descriptor below through the host's existing standard stdio MCP facility.

Pilot scope:

- explicit local save, context/search, inspect, correction, deletion preview and confirmation, status, and export;
- one local data directory and one application scope per customer;
- synthetic data or a specifically reviewed native bigfeels v1 export;
- no automatic conversation capture or extraction worker;
- no provider configuration, embeddings, remote recall, or loopback HTTP service;
- no live Microsoft 365 or Scout corpus ingestion.

The actual Scout corpus and its export contract were not available for this work. Do not infer a migration mapping from examples in this document.

## Explicit-only server

Use `mcp --explicit-only` for the pilot. This mode:

- omits `memory_observe` and `memory_process` from `tools/list`;
- rejects calls to either omitted tool;
- rejects combination with `--url`;
- bypasses provider/config loading, so it neither configures extraction/embeddings nor makes provider-backed recall calls;
- retains `memory_remember`, `memory_context`, `memory_search`, `memory_inspect`, `memory_correct`, `memory_forget_preview`, `memory_forget`, `memory_status`, and `memory_export`.

Recall in this mode is local lexical retrieval against the selected customer directory and scope.

## Exact stdio launch descriptors

The argument order is significant: Python module selection comes first, the global `--data-dir` option precedes the `mcp` subcommand, and MCP options follow the subcommand.

### Windows customer example

```json
{
  "command": "C:\\Program Files\\BigFeels Scout\\venv\\Scripts\\python.exe",
  "args": [
    "-m",
    "bigfeels_mem.cli",
    "--data-dir",
    "C:\\Users\\Scout Service\\AppData\\Local\\BigFeels Scout\\Customers\\Contoso",
    "mcp",
    "--explicit-only",
    "--space",
    "customer:contoso"
  ]
}
```

JSON array elements preserve paths containing spaces. Do not add shell quotes inside an element. The Python 3.11+ environment named by `command` must contain the installed wheel.

### POSIX customer example

```json
{
  "command": "/opt/BigFeels Scout/venv/bin/python",
  "args": [
    "-m",
    "bigfeels_mem.cli",
    "--data-dir",
    "/var/lib/bigfeels-scout/customers/fabrikam",
    "mcp",
    "--explicit-only",
    "--space",
    "customer:fabrikam"
  ]
}
```

Run a separate process for every customer. Do not point two customers at the same directory, and do not grant one process both customer scopes. Application scopes limit core operations but are not an operating-system sandbox; filesystem permissions remain the stronger boundary.

## Preflight and privacy disclosure

Run Doctor with the same Python executable and data directory before connecting the host:

```powershell
& "C:\Program Files\BigFeels Scout\venv\Scripts\python.exe" -m bigfeels_mem.cli --data-dir "C:\Users\Scout Service\AppData\Local\BigFeels Scout\Customers\Contoso" doctor
```

Doctor reports the running Python version and minimum, path permission observations, and that filesystem locality and synchronization are not verified. On Windows, `os.chmod` does not configure or prove ACL security; Doctor reports the ACL as unverified. An administrator must apply and review the directory ACL for the service identity. On POSIX, Doctor can report mode bits, but it does not verify ACLs or storage-system enforcement.

Use local, nonsynced storage where policy requires it. Network mounts, cloud-synced folders, backups, snapshots, and other processes can retain or copy the plaintext database, WAL, config, and exports. Bigfeels does not claim to detect or prevent those copies.

## Authority and approved corpus boundary

The operational ledger remains authoritative for tasks, approvals, execution watermarks, and receipts. Memory is contextual evidence only; it must not create approvals, advance watermarks, or replace ledger receipts. This pilot does not define a CCfS schema or adapter.

The only approved corpus interchange boundary is the native bigfeels **version 1 export bundle** produced by `bigfeels-mem export` and accepted directly by `bigfeels-mem restore`. Do not wrap the bundle or extract a nested payload. `export --output PATH` writes the bundle to `PATH`; its stdout JSON is only an operational status receipt and is not restore input. The native envelope has top-level `version: 1`, `exported_at`, `spaces`, `evidence`, `memories`, `tombstones`, `supports`, `relations`, and `jobs` fields.

Within the scopes exported, v1 preserves record IDs, source identities, validity/recorded timestamps, memory revisions and statuses, evidence links, correction/supersession relationships, and tombstones. It does not contain HTTP credentials or derived vector indexes. The v1 job export intentionally carries the compatibility job shape; terminal failed jobs are exported as pending and provider diagnostic detail is not a durable corpus guarantee.

No claim is made that a Scout or Microsoft 365 corpus can be converted to this envelope. Such a migration requires the real source contract, data-owner approval, and a separately reviewed adapter.

## Backup, restore, and rollback

1. Stop writes to the source directory for the backup window.
2. Export the approved source scopes to a new private backup file. Export refuses to overwrite an existing file.
3. Retain the source data directory unchanged.
4. Restore only into a newly chosen, empty customer directory.
5. Run Doctor, then search and inspect representative IDs and correction history in the restored directory before changing any launch descriptor.
6. Roll back by stopping the candidate process and restoring the previous launch descriptor. If rollback requires an archived export, restore it into a new rollback directory and point the descriptor there. Never restore over the prior or candidate directory; use another new directory for every restore attempt.

Example using separate directories:

```sh
python3.11 -m bigfeels_mem.cli --data-dir /private/contoso-source export --space customer:contoso --output /private/backups/contoso-v1.json
python3.11 -m bigfeels_mem.cli --data-dir /private/contoso-restore-001 init
python3.11 -m bigfeels_mem.cli --data-dir /private/contoso-restore-001 restore /private/backups/contoso-v1.json
python3.11 -m bigfeels_mem.cli --data-dir /private/contoso-restore-001 doctor
```

Restore is not a merge operation. It rejects a store containing evidence or tombstones, and it does not reconcile concurrent revisions or deletion histories.

## Pilot acceptance record

Record outside bigfeels memory:

- approved customer and scope;
- exact wheel/version and Python executable;
- launch descriptor and private data-directory identity;
- administrator ACL/storage review;
- backup location and checksum receipt under the organization's secret-handling policy;
- v1 export scope and representative restored IDs/history checked;
- host connection result and execution watermark;
- rollback descriptor and owner.

These are operational-ledger entries, not memories and not inferred approvals.
