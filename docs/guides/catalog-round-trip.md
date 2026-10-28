# Review and import an editable catalog

Excel and CSV let collaborators review semantic definitions without making the
workbook a second source of truth. Canonical YAML and macros remain authoritative.
JSON is read-only and cannot round-trip.

## Safe first edit

1. Begin on a clean review branch. Import any wanted edits from an existing
   workbook **before** regenerating it.
2. Run `export catalog` and open the workbook. The Jaffle Shop workbook is
   `examples/jaffle_shop/catalog/catalog.xlsx`.
3. On `gld`, change the Description on the `customer_lifetime_value` output-column
   row of `gld_customers`. Leave identities, expressions and other metadata intact.
4. Save and close Excel, then preview:

```sh
uv run slbt --project-dir examples/jaffle_shop import examples/jaffle_shop/catalog/catalog.xlsx --dry-run
```

Import writes by default. After checking the preview, apply the import:

```sh
uv run slbt --project-dir examples/jaffle_shop import examples/jaffle_shop/catalog/catalog.xlsx
git diff -- examples/jaffle_shop
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run slbt --project-dir examples/jaffle_shop export catalog
```

The preview lists file actions, not a full content diff. Review actual diffs and
run dbt tests when changing logic. Untouched imports report zero changes and
preserve canonical bytes. An entire selected proposal must validate before writes.

## Editing rules

- Preserve every selected resource; renaming, moving and deletion are unsupported.
- Keep TABLE → STEP/OUTPUT → COLUMN hierarchy and output order intact.
- SQL and JSON must be plain text, not Excel formulas or error cells.
- Config and Meta are JSON objects; Tests and Constraints are JSON arrays.
- Key/References summaries must agree with their native definitions.
- Dependencies are inferred from expressions; do not author a separate list.
- Inherited defaults stay inherited; review effective versus explicitly authored
  configuration before overriding it.
- Use XLSX to edit macros, defaults and classifications; CSV handles model/source
  review. Import a CSV directory, not a single sheet file.
- If several catalogs exist, pass `--catalog NAME` on import. Export accepts
  multiple names.

See [catalog reference](../reference/catalogs.md) for column layout and JSON.
Close Excel before export to avoid Windows file locks. Regeneration can overwrite
unimported edits. Import staging/rollback is best effort, not a crash-safe
multi-file transaction; do not allow concurrent writers.
