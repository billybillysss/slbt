# Production adoption checklist

This is an adoption checklist, not a production-readiness certification. slbt is
currently 0.2.0. Validate its behavior on your data, dialect, adapter and deployment
environment before using it for a production workload.

## Reproducible build

- Pin the slbt version and Python/runtime dependencies. Keep `uv.lock` when
  working from this checkout; use `uv sync --locked` in CI.
- Version canonical YAML, macros, classifications and project settings together.
- Pin dbt, adapter and packages independently in your integrating project.
- Start from a clean generated output directory or deliberately review stale
  output cleanup. Never mix handwritten work into managed export scope.
- Run one writer per canonical project/output root; there is no concurrency lock.

## Review gate

1. Review human/AI-authored changes and business assumptions.
2. Run offline `validate`, inspect affected logic and preview any canonical
   `optimize`, `enrich`, `import` or classification-sync changes.
3. Apply approved changes on a branch and inspect the Git diff. A dry-run is a
   file-action preview, not a complete content diff.
4. Export dbt into dedicated build directories and run dbt parse/build/tests with
   a controlled profile. Test joins, grain, nulls, monetary units and edge cases.
5. Review contracts/constraint support and snapshot behavior on the real adapter.
6. Refresh JSON, editable catalogs and dbt docs as required, after importing
   wanted workbook edits. Record build versions, logs and test results.

The [demo](../../examples/jaffle_shop/README.md) proves one local DuckDB workflow,
not every warehouse. SQLGlot dialect availability is not an adapter support matrix.

## File mutation and deployment

| Operation | Existing protection | Remaining boundary |
| --- | --- | --- |
| Canonical import/optimization/enrichment | Validate proposal before writes; stage files; attempt rollback on replacement failure. | Not an atomic multi-file transaction. Rollback can fail; crashes may leave partial state. |
| Export | Validate outputs, collisions and paths before writes; explicit prune. | Writes/deletes are per-file with no rollback. Filesystem errors can leave partial outputs. |
| Classification sync and initialization | Validate/refuse conflicting inputs as applicable. | Direct writes, not a multi-file transaction. |
| Paths | Reject unsafe overlap/traversal, symlinks and supported junction cases. | Not a sandbox or protection against all filesystem races. |

Keep Git history/backups for canonical inputs. Build generated artifacts in a
dedicated staging directory and promote only a successful build using your
deployment platform's atomic/release mechanism. Do not deploy while export is
writing. After interruption, inspect canonical files, revalidate and regenerate
outputs before continuing; do not assume automatic recovery.

## Security and operations

- Do not store credentials in definitions or generated catalogs. Review metadata
  before sharing with external AI providers.
- Do not run untrusted dbt macros/projects with production credentials. slbt's
  offline processing does not make later dbt execution safe.
- Keep generated outputs under controlled paths and use least-privilege database
  roles for execution.
- Treat runtime macro skips and unresolved enrichment properties as review items,
  even if a command exits successfully.
- On upgrades, compare generated SQL/JSON and result tests before rollout.

See [security](../../SECURITY.md), [compatibility](../compatibility.md),
[testing](../development/testing.md) and [release checks](../development/releasing.md).
