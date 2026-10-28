# Changelog

User-visible changes are recorded here. This file starts with the current
documentation/repository-hardening work; it does not reconstruct prior releases.

## Unreleased

### Added

- Resolved JSON model/step columns include SQL expressions and direct upstream
  column lineage, with explicit partial/deferred analysis status.
- SVG logos and icons with light/dark variants.
- MIT project license and distribution license metadata, preserving separate
  third-party seed licensing.
- Semantic Layer Build Tool documentation, reproducible YAML → dbt SQL → JSON
  examples, AI-assisted review guidance and production-adoption boundaries.
- Contributor, security, compatibility and release guidance.
- `slbt --version` for diagnostics.
- Documentation checks, mutation failure regression tests, distribution smoke
  checks and expanded Python/platform CI targets.

### Changed

- Corrected `--preserve-macro`, the `optimization.preserve_macro` configuration
  key and the `optimize_project(preserve_macro=...)` keyword. The old `marco`
  spellings remain supported as legacy aliases; values and defaults are unchanged.
  Rename existing uses to migrate, without supplying both configuration keys or
  Python keywords together.
- Inspection SQL and logical plans now share canonical optimization's
  collision-safe alphabetic generated-relation names, preserving authored
  aliases and output column names instead of exposing an internal wrapper name.
- README and package description now emphasize unified transformation semantics,
  business metadata, inspectable logic and machine-readable outputs.
- Source distributions include documentation, example fixtures and maintenance
  scripts needed for a useful source checkout.
- Refreshed the bundled Jaffle Shop workbook from canonical definitions so its
  no-op import and current-export regression checks agree.
- Removed the Fabric Spark adapter from development dependencies; local dbt
  integration checks use DuckDB.
- Excluded SQLGlot 30.25.0 after fresh-install tests exposed a `QUALIFY` alias
  regression. CI now runs the full suite against the installed wheel with fresh
  dependencies as well as the locked development environment.

## Current package version

The source currently declares `0.2.0`. This is not a claim that the Unreleased
changes have been published to PyPI or tagged as a release.
