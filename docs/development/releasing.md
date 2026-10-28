# Release checklist

This is a maintainer checklist, not an automated publishing workflow. Do not tag,
upload packages, rename the repository or announce a release without maintainer
approval.

## Before preparing a release

- Review [compatibility](../compatibility.md), security/dependency changes and
  unresolved issues. Record user-visible changes and migrations in
  [CHANGELOG](../../CHANGELOG.md).
- Set the intended version in `src/slbt/__init__.py`; update version-specific
  documentation/examples and refresh `uv.lock` with `uv lock` when metadata changes.
- Confirm the MIT license and bundled seed Apache 2.0 notices are present. Keep
  attribution intact; project licensing does not replace third-party licensing.
- Run the full suite, documentation checks, executable demo and handwritten dbt
  fixture. Confirm CI results for declared Python/platform targets.

## Build and inspect

```sh
uv sync --locked
uv run pytest
uv build
```

Use a clean build directory so stale artifacts cannot be mistaken for the new
version. Run the [distribution smoke check](testing.md#distribution-checks) using
both wheel and source-archive installations in an isolated environment. Confirm
version, CLI initialization/validation/export, MIT metadata, documentation and
example fixture inclusion. Never infer installability solely from editable tests.

## Validate a candidate

- Regenerate representative dbt SQL/properties, JSON and editable catalogs.
- Compare canonical rewrite diffs and dbt results with the previous version.
- Check deferred runtime macros, contracts, snapshots and adapter-specific tests.
- Record known limits: no native AI service, no warehouse performance guarantee,
  no atomic multi-file writes and no universal adapter certification.
- Check repository URLs, installation instructions and release notes. Do not add
  package/download badges before a real release exists.

Only after approval should maintainers choose a tag, publish artifacts and link
the release notes. That external publishing step is deliberately not automated
by this change. After release, move approved Unreleased notes to a dated version
section and leave a new Unreleased section.
