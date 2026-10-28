# Project configuration

Project resolution uses `--project-dir`, then the exact environment variable
`slbt_PROJECT_DIR`, then searches upward for `slbt_project.yaml`. Put the global
option before the command. Configured paths are relative to that file.

```yaml
version: 1
model_path: models/
macro_path: macros/
catalog_path: catalog/
optimization:
  dialect: duckdb
  format: pretty
  preserve_marco: true
  enrich: false
dbt:
  path: dbt/
  format: pretty
  default_configs:
    name: defaults
    configs:
      - name: gold_defaults
        target:
          layer: gold
        config:
          materialized: table
catalog:
  - name: catalog
    macros:
      - name: macros
    include:
      models:
        - ./
      macros:
        - ./
    sheets:
      - name: sources
        type: source
      - name: gld
        type: model
        includes:
          layer: gold
```

This example assumes the Gold classification and relevant resources already
exist; `init` creates a smaller runnable project. See the complete
[Jaffle Shop configuration](../../examples/jaffle_shop/slbt_project.yaml).

## SQL settings

- `optimization.dialect` controls SQL parsing/rendering, not translation of
  already-authored dialect-specific expressions. The initializer uses `spark`;
  the Jaffle Shop demo explicitly uses `duckdb`.
- `optimization.format` accepts `pretty` or `compact`.
- **`preserve_marco` is the current public spelling**, including the CLI option
  `--preserve-marco`. Do not substitute `preserve_macro` or `preserve_macros`.
- `optimization.enrich` controls metadata enrichment during optimization.
- `dbt.format` accepts `pretty`, `compact` or `source`. `source` attempts to
  preserve eligible raw SQL formatting while lowering logical relations; it is
  not a promise to preserve all original text.

Invocation-level optimization flags override project settings. See the
[CLI](cli.md) and [optimization guide](../guides/inspection-and-optimization.md).

## Defaults and classifications

Default rules select `layer`, `domain` and/or `tables`. Selector keys are ANDed;
matching configuration merges recursively, with explicit model configuration
overriding inherited values. Catalog imports keep inherited values inherited,
rather than copying all effective defaults into each model.

Models and sources must use layers/domains registered in
`models/classifications.yaml` (under the configured model root):

```yaml
version: 1
layers:
  - name: gold
    description: Business-facing models.
    domains:
      - name: sales
        description: Sales and payment reporting.
```

`sync-classifications --dry-run` previews syncing Silver domains into Bronze. That
command requires both layers; it is not a general-purpose registry generator.

## Paths and catalog selection

Use portable forward slashes in configuration. Input paths cannot be absolute or
traverse outside their permitted roots. Output paths can use permitted relative
destinations, but inputs and generated outputs must not overlap. Unsafe symlinks
and supported junction checks are rejected. These checks are safeguards, not an
OS sandbox; see [security](../../SECURITY.md).

Each catalog has a name, model/source sheets and optional macro sheets.
`include.models` and `include.macros` scope files; sheet `includes` selectors
choose classified resources. The default export format is XLSX; use
`export catalog --format csv|json` for other representations.
