# Mnemosyne migration and default-profile cutover

`bigfeels-mem import-mnemosyne` accepts the UTF-8 JSON **v1.3 export** produced by `mnemosyne-memory 3.15.1`. It reads the file and imports into a separate, empty bigfeels data directory. It does not open the Mnemosyne database, change Hermes configuration, install a plugin, start a model, or switch a running provider.

The migration is one-time. The active **default Hermes profile** is the rollout boundary. Other profiles must be backed up and left on their existing providers. Do not assume a shell's current profile is the default.

## Prepare the source and rollback point

1. Identify the default profile's actual home, current provider configuration, plugin directory, and Mnemosyne database. Record any environment overrides. Back up the other profiles without changing them.
2. Stop writers for the selected profile while taking a consistent backup. Use SQLite's backup API from a read-only connection, or a stopped, complete database backup. Copying only a live WAL database's main file can omit committed records.
3. Retain the original database, its backup, and the default profile's configuration/plugin backup. Preserve any uncommitted local plugin or migration additions; do not force-reinstall over them.
4. Use an existing v1.3 export, or run the installed Mnemosyne export API **against a disposable copy of the consistent snapshot**, with an isolated Mnemosyne/Hermes home. `Mnemosyne.export_to_file()` is the relevant API. Mnemosyne constructors/export helpers can initialize tables, so do not initialize them against the original source as part of this procedure.
5. Keep the export and eventual receipt private. They contain plaintext data or source identifiers. Select a new bigfeels directory; do not reuse another profile's store.

The importer supports the JSON-export option of issue #3. It does not accept a raw SQLite file. The source contract was checked against `mnemosyne/core/memory.py:export_to_file`, `beam.py:export_to_dict`, and `canonical.py:export_all` in the published 3.15.1 wheel (SHA-256 `bc79a6277d2195bba59123292924e8ff5421357c58ee543a23054b8dd97d7484`).

## Validate, import, and verify

Install bigfeels from the reviewed checkout using [INSTALL.md](../INSTALL.md). These paths are examples and must be replaced with the selected default profile's paths:

```sh
bigfeels-mem --data-dir /private/bigfeels-default-import import-mnemosyne \
  /private/backups/mnemosyne-v1.3.json \
  --source-id hermes-default --space owner \
  --receipt /private/backups/bigfeels-import-receipt.json \
  --naive-timezone America/New_York --dry-run
```

Choose the **source machine's actual timezone** for timestamps without offsets. The example is not a default. Omit the flag when every source timestamp is aware. `UTC`, fixed offsets such as `+02:00`, and IANA names are accepted. Use `--naive-timezone=-05:00` for a negative offset. Named zones require the system's timezone database (or the optional Python `tzdata` package on systems without one). Ambiguous or nonexistent local times are rejected; resolve their offsets from source evidence rather than guessing.

The dry-run validates all rows and returns source/excluded counts, fingerprints, and destination spaces without creating files. Run the same command **without `--dry-run`** to import. The receipt is written privately and atomically after the database transaction. If receipt publication is interrupted, repeat the same command to verify the imported database and regenerate it.

Run the command once more after a successful import. It must report `already_imported` with the same fingerprints and verified count. Every native evidence, memory, support, and relation row is compared to the expected import; source/import counts alone are insufficient. Existing memories, corrections, tombstones, extraction jobs, or embeddings that differ from the plan cause refusal, never replacement or resurrection.

Keep the receipt. It records each source table/ID, destination memory/evidence ID, scope, content hash, exact source-row hash, input-file hash, and target-data hash. Standard output contains counts and hashes, not source content. Compare `source_records` and `verified_records`, inspect several mapped IDs, and test both current and historical recall:

```sh
bigfeels-mem --data-dir /private/bigfeels-default-import inspect MEMORY_ID --space SPACE_FROM_RECEIPT
bigfeels-mem --data-dir /private/bigfeels-default-import context 'selected synthetic or known fact' --space SPACE_FROM_RECEIPT
bigfeels-mem --data-dir /private/bigfeels-default-import context 'historical fact' --as-of 2026-01-15T00:00:00Z --space SPACE_FROM_RECEIPT
bigfeels-mem --data-dir /private/bigfeels-default-import doctor
```

Doctor may report `needs_attention` because Windows ACLs are not verified. Review the database/runtime fields and secure the paths independently. Do not upload the source export, database, or private receipt to GitHub.

## Mapping and limits

| Source | Destination |
| --- | --- |
| Working, episodic, legacy rows | One native memory and source-evidence record per source table/ID |
| Canonical rows | One memory per version, original revision and validity, linked supersession history |
| Global scope | The required `--space` argument |
| Session scope, including legacy rows without scope | A separate deterministic `mnemosyne:…:session:…` space per source session |
| Canonical owner | A separate deterministic `mnemosyne:…:owner:…` space per owner |
| Source row and export metadata | Exact values retained in JSON evidence; original timestamp strings and `metadata_json` survive |
| Superseded record | Linked to its replacement; missing/ambiguous/cross-scope replacements are rejected |
| Original inferred trust | Candidate/inferred memory; other imported records remain historical direct claims, with outcome unspecified |
| Embeddings, scratchpad, consolidation/sync logs, triples, annotations | Excluded as user memories; section counts reported |

The command never collapses session/owner scopes into global access. Add only deliberately approved spaces from the receipt to a host's configuration. Legacy and working rows with the same original ID remain separately addressable by table. Episodic `summary_of` and metadata source references are preserved verbatim as provenance; they are not treated as verified tool observations or automatically expanded into cross-scope access.

The input limit is 64 MB, authored content is limited to 16,000 characters per row, and serialized evidence to 100,000 characters per row. Invalid versions, missing sections, duplicate IDs/JSON keys, nonfinite numbers, invalid validity/history, or unsupported scope values fail before destination creation. Content is not truncated, re-extracted, or rewritten. Source and receipt paths cannot alias the destination database, config, or SQLite journal files. Ordinary replacement histories retain the source invalidation time even when it follows creation of the replacement; inferred candidates remain excluded from normal historical recall. A source update requires a new migration decision and fresh destination, not a merge into an active store.

## Install and activate only the default profile

The repository root already contains the supported Hermes user-plugin manifest and entry point. No Hermes core/runtime patch is required. Set the intended profile home explicitly for each command (POSIX syntax below; use the equivalent process environment on Windows):

```sh
HERMES_HOME=/absolute/default-profile-home hermes plugins install promptclickrun/bigfeels --enable
```

For an existing installation, preserve its backup/local changes and use Hermes's normal update flow after reviewing those differences. Configure **only that profile's** `plugins.bigfeels` settings before activating:

```yaml
plugins:
  bigfeels:
    data_dir: /private/bigfeels-default-import
    capture_roles: []
    auto_extract: false
    # Add approved session/owner spaces from the receipt only when needed:
    project_spaces: []
```

Global imports to `owner` are available by default. Imported session and canonical-owner memories stay isolated until their spaces are deliberately selected; do not paste every receipt space into every profile.

```sh
HERMES_HOME=/absolute/default-profile-home hermes memory setup bigfeels
HERMES_HOME=/absolute/default-profile-home hermes memory status
```

Start a new session, or restart only that profile's gateway through its normal lifecycle. Confirm the active provider and data path, then exercise native search/inspect with known mapped IDs and a harmless save/correct/preview/forget cycle. Verify other profile configurations and providers match their backups. Keep capture and extraction disabled during migration validation.

## Rollback

Stop the selected bigfeels session/gateway. Restore the backed-up **default profile** configuration and, if changed, its prior plugin directory, or select the previously recorded Mnemosyne provider using Hermes's normal memory setup command. Point it to the original untouched database and start a new session. Verify its provider identity and known recalls. Do not restore or switch other profiles.

Retain the bigfeels directory and receipt for diagnosis; do not merge post-cutover writes back into Mnemosyne automatically. Their reconciliation is separate work. Backups may retain plaintext even after logical deletion.

## Verification boundary

Repository tests use synthetic exports and temporary stores. The root plugin loader was exercised against Hermes commit `44ddc552f5e054759a6970af8997ea588a9d81c9`. These checks establish code and installation compatibility, not a completed migration of the user's memories. Issue #3 stays open until the actual default-profile import, receipts, recall checks, cutover, and rollback checkpoint are verified on the host that owns that data.

Local verification on 2026-10-05: 146 Python tests passed with the pinned Hermes checkout and no skips; 27 Node tests passed. Deterministic replay covered all three expected memories with zero forbidden exposures and three correct abstentions. A fresh wheel completed dry-run/import/idempotent replay. The official Hermes installer loaded a committed local repository into a temporary profile, where save/search/inspect/correct/preview/forget passed. These are synthetic checks, not a live default-profile rollout or fresh hosted Windows/macOS CI.
