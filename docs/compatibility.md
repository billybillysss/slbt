# Compatibility and versioning

## Current boundaries

| Surface | Current contract |
| --- | --- |
| Package | Version `0.2.0`; pre-1.0. Pin and test upgrades rather than assuming all minor updates are compatible. |
| Python | Metadata requires 3.11+. CI targets 3.11 and 3.13 on Linux, plus 3.13 on Windows. Other Python/platform combinations are not thereby certified. |
| SQLGlot | Runtime dependency is constrained to `>=30.0,<31,!=30.25.0`. Dialect availability does not establish warehouse execution support. |
| Canonical YAML | Document/project `version: 1`. Strict fields and duplicate-key rejection. |
| Resolved JSON | `format: slbt.catalog`, `format_version: 1`, `representation: resolved`. Consumers must check the envelope and tolerate optional metadata. |
| dbt | Ordinary generated SQL/properties/snapshots. Execution compatibility depends on your dbt and adapter versions. The bundled executable demo uses DuckDB. |
| Python internals | Not a separately guaranteed stable public API. Prefer the CLI or explicitly version-tested integrations. |

CI declares test targets; a new matrix entry is not proof it has passed until
that workflow runs successfully. The development environment uses DuckDB for
local execution checks; it does not run a remote warehouse integration.
No macOS CI or warehouse performance benchmark is currently provided.

SQLGlot 30.25.0 is excluded because a fresh installation fails existing
inspection tests for window aliases in `QUALIFY`. The same Python 3.11 test
environment passes with 30.24.0. The exclusion is not a general certification of
every other SQLGlot version; CI also tests a freshly resolved installed wheel.

## Upgrade policy

Before upgrading, keep a canonical baseline and regenerate outputs in an isolated
directory. Compare validation, SQL, properties, catalog JSON, mutation diffs and
dbt results. Check [CHANGELOG](../CHANGELOG.md) for documented changes. Do not
assume every historical behavior has a migration tool.

The existing `preserve_marco` / `--preserve-marco` spelling is intentional for
compatibility. A future correction must include an explicit migration path;
aliases with different spellings are not currently supported.

## Maintainer policy for future changes

Record user-visible changes under Unreleased. Document CLI/config/schema changes,
add regression tests, and provide migration instructions for incompatible changes.
If the JSON contract becomes incompatible, update its format version rather than
silently reinterpreting existing fields. Additive fields should not require
consumers to reject an otherwise supported envelope. This is a maintenance rule,
not a claim of a general JSON Schema validator or an existing migration service.
