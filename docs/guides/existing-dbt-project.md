# Integrate with an existing dbt project

Adopt incrementally. slbt emits ordinary models, macros, source properties,
folder-level model properties and model-owned snapshots; it does not generate
your database profile or take over dbt execution.

## Establish ownership first

1. Create a separate canonical project with `slbt init`.
2. Set its dialect and register classifications, sources and model definitions.
3. Choose a dedicated generated root, separate from canonical inputs and
   handwritten dbt models/macros/snapshots.
4. Point dbt's search paths at the generated category directories, retaining
   existing handwritten paths.

For example, with `dbt.path: ../warehouse/generated_slbt/`, a dbt project at
`../warehouse/` can include these entries in its existing `dbt_project.yml`:

```yaml
model-paths:
  - models
  - generated_slbt/models
macro-paths:
  - macros
  - generated_slbt/macros
snapshot-paths:
  - snapshots
  - generated_slbt/snapshots
```

Adapt paths to your layout. Do not replace the rest of your dbt configuration.
Keep generated names unique across the dbt graph. The
[demo dbt project](../../examples/jaffle_shop/dbt/dbt_project.yml) keeps tracked
execution configuration, seeds and handwritten tests in the export root, alongside
dedicated generated `models/`, `macros/` and `snapshots/` subfolders.

## Preview and integrate

From your slbt project, using its installed CLI:

```sh
slbt validate
slbt export dbt --dry-run
slbt export dbt
```

Then run `dbt parse`, `dbt build` and your adapter-specific checks from the
integrating dbt project with an appropriate profile. slbt never invokes them
automatically. Supply seeds, packages and runtime macros required by that project.
Test contracts, constraints and snapshots on the actual target adapter.

Local transformations become CTEs; refs/sources become ordinary dbt Jinja calls.
Export preserves relative model/macro folder layout. Descriptions, tests, types,
source bindings and supported configuration become dbt properties. Generate
documentation through dbt after a successful integration build.

## Scope and cleanup

`export dbt --subpath gld/optimization` selects an existing input folder relative
to model/macro roots. It does not include upstream dependencies automatically;
the integrating dbt project must already provide them. Every input is still
validated before output generation.

Exports do not delete stale files by default. Preview `--prune --dry-run` before
intentional cleanup. Pruning can remove **any file** in the managed dbt scope,
not only files slbt previously generated. Never place handwritten work there.
For deployment, build into a clean dedicated directory and publish it only after
validation and dbt checks; do not rely on export for atomic multi-file deployment.
