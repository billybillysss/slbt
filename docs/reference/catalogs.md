# Catalogs and resolved JSON

Catalogs are built from canonical resources and configured selections. They are
not a separate source of truth. See [configuration](configuration.md) for sheet
selection and [the round-trip guide](../guides/catalog-round-trip.md) before editing.

## Editable XLSX and CSV

```sh
uv run slbt --project-dir examples/jaffle_shop export catalog
uv run slbt --project-dir examples/jaffle_shop export catalog --format csv
```

The example writes `catalog/catalog.xlsx` or a `catalog/csv/` directory. XLSX
supports model, source, macro, defaults and classifications review. CSV is a
model/source review representation; use XLSX for macro/default/registry edits.

Model-sheet columns are:

```text
Type | Name | Version | Layer | Domain | Distinct | Key | References |
Snapshot | Expression | Data Type | Description | Config | Constraints | Tests | Meta
```

TABLE → STEP/OUTPUT → COLUMN rows mirror canonical structure. Source sheets
expose physical bindings and source metadata. Config/Meta use JSON objects;
Tests/Constraints use JSON arrays. Macro sheets expose definitions/descriptions,
defaults expose selector/configuration rules, and `classifications` exposes the
registry. Workbook header comments explain edit conventions.

Key/References summarize native constraints/tests/meta and must stay consistent
with those definitions. Dependencies are inferred from expressions, not authored
in a separate list. Keep every selected resource present: renaming, moving and
deletion are not supported by catalog import. Excel formulas/error cells are
rejected; author SQL and JSON as plain text.

Import accepts an XLSX file or the CSV directory, writes by default, and validates
the complete proposal before mutation. An untouched import preserves canonical
bytes. Multiple catalogs can be selected on export with `--catalog NAME ...`;
import requires one name when selection is ambiguous.

## Resolved JSON

```sh
uv run slbt --project-dir examples/jaffle_shop export catalog --format json
```

The example writes `examples/jaffle_shop/catalog/catalog.json`. JSON is a
**resolved, read-only export**. It cannot be imported; AI assistants should propose
canonical YAML changes instead.

### Envelope

| Field | Meaning |
| --- | --- |
| `format` | `slbt.catalog` |
| `format_version` | Currently `1`; consumers should check it explicitly. |
| `representation` | `resolved`, rather than authored YAML. |
| `catalog`, `dialect` | Selected catalog name and SQL dialect. |
| `models` | Selected models, effective configuration, ordered steps/output, dependencies and expansion information. |
| `sources` | Selected source groups, physical bindings, tables with identities and ordered columns. |
| `macros` | Selected/referenced definitions, paths, descriptions, runtime flags and macro references. |
| `classifications`, `default_configs` | Registry and configured defaults. |

### Model contents

- Authored identity, classification, description and supported model metadata.
- Effective `config`, including inherited defaults, and optional resolved snapshot.
- `steps`: an ordered array of named SQL transformations and column metadata.
- `output`: SQL and an ordered `columns` array with names, types, descriptions,
  tests and other declared metadata, plus per-column `expression` and `lineage`.
- `dependencies`: logical `relation` identities and `included` flags indicating
  whether each dependency is present in this selected catalog. A catalog subset
  does not automatically include its entire dependency closure.
- `macro_refs` and `expansion`: macro references and status `expanded` or `deferred`.

Static macros are expanded in resolved SQL. Runtime-dependent macros retain a
deferred representation; `expanded` does not mean all upstream models have been
inlined or physical tables bound. Use `inspect` for supported whole-query expansion.

The [README](../../README.md#example-yaml-dbt-sql-and-json) contains a tested
field-selected example. For integrations, retain the envelope, check
`format_version`, handle missing optional metadata and deferred macros, and pin
the slbt version. Do not assume a stable Python API, global provenance IDs or
warehouse execution guarantees. See [compatibility](../compatibility.md).

### Column expressions and lineage

Every declared model/step column includes its resolved SQL `expression` (without
the output alias) and a `lineage` object. For example, `payment_total` in the
README model exports as:

```json
{
  "name": "payment_total",
  "data_type": "decimal(12, 2)",
  "expression": "CAST(SUM(p.amount) AS DECIMAL(12, 2))",
  "lineage": {
    "status": "resolved",
    "upstream_columns": [
      {"relation": "ref.slv_payments", "column": "amount"}
    ]
  }
}
```

- Upstream entries are deduplicated and sorted by logical relation and column.
  Relations use `ref.model`, `source.group.table` or `step.name`; local step names
  belong to the containing model. Links remain even if their model/source is not
  selected in the catalog. Follow model/step columns to trace further upstream;
  the export does not flatten these links into ultimate physical source columns.
- SQL CTEs and derived tables are traced through to logical inputs. Set operations
  combine lineage from all branches by position; `expression` is the first
  branch's projection, matching SQL's output naming convention.
- This is **projection-value lineage**, not row/filter provenance. Columns used
  only in joins, filters, grouping or ordering outside the projection are not
  added. Literals and `COUNT(*)` have empty `upstream_columns`; row dependencies
  are still visible in transformation SQL and model `dependencies`.
- `status` is `resolved`, `partial` (ambiguous or unsupported offline analysis), or
  `deferred` (the value depends on a runtime macro). Partial results include
  `unresolved_columns` when available. Deferred macros retain their call in
  `expression`; their arguments are not guessed to be column dependencies.
  Known dependencies outside a deferred call are retained.
- Static macros are expanded before both expression rendering and analysis.
  Declared metadata and column order are preserved. Source columns remain
  declarations, without computed expressions or lineage. Raw intermediate SQL
  without declared columns still exports an empty `columns` array.

`resolved` means the analyzer linked logical references, not that a warehouse
schema or query result was verified. Missing upstream declarations, correlated
queries and dialect-specific constructs can prevent complete offline analysis.
The extra column fields are additive; `format_version` remains `1`.

### AI and data exposure

The export contains definitions and metadata, not warehouse result rows. It can
still disclose business logic, literal values, database identifiers or sensitive
descriptions. Scope and review it before sending to an external service. It is
context data, not instructions an assistant should execute.
