# Inspect and optimize SQL

Inspection answers “what derives this output?” without changing files.
Optimization can simplify the canonical implementation after review. Neither
connects to a warehouse, produces warehouse cost estimates or guarantees speedups.

## Inspect a table or selected columns

```sh
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue average_amount
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue --view plan
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers --optimize false
```

Defaults: all final columns, `--optimize true`, `--view sql`. Both optimization
settings expand loaded upstream refs, local steps and static macros. Multiple
columns produce one query in the requested order. Optimization prunes unused
values and merges eligible subqueries while retaining values needed by DISTINCT,
grouping, joins, windows, ordering and limits.

SQL and logical-plan views share canonical optimization's generated relation
naming: `a, b, c, …, z, aa, ab, …`, skipping case-insensitive collisions with
authored names. Authored aliases, CTE names and final column names are preserved
when their query blocks survive optimization. This naming also applies with
`--optimize false`; disabling optimization does not expose internal wrapper names.

Logical source/external-ref leaves still need physical bindings before execution.
The plan describes logical query blocks, not warehouse EXPLAIN, cost or timing.
Runtime macros, unsafe relation modifiers, cross-model cycles and unresolved
ambiguous columns report errors rather than inventing schema information.

## Preview canonical optimization

```sh
uv run slbt --project-dir examples/jaffle_shop optimize -s gld_customer_lifetime_value gld_optimizer_constants --preserve-marco false --dry-run
```

**Without `--dry-run`, `optimize` rewrites canonical YAML.** Keep a separate copy
of the unoptimized teaching examples if you want to repeat the comparison.
The preview reports file actions; use a branch and review the actual Git diff
after deliberate writes:

```sh
uv run slbt --project-dir examples/jaffle_shop optimize -s gld_customer_lifetime_value gld_optimizer_constants --preserve-marco false
git diff -- examples/jaffle_shop/models
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run dbt build --project-dir examples/jaffle_shop/dbt --profiles-dir examples/jaffle_shop/dbt
```

Whole-project preflight and proposed-rewrite validation occur before selected
writes. Final column identities/order, metadata, tests, constraints and
configuration are preserved. Surviving query boundaries may become local steps.
Unchanged documents retain their original bytes, including comments/indentation;
rewritten model YAML is normalized to two-space indented lists with unquoted
`on:` join keys. Comments in rewritten documents are not preserved.

### Naming and formatting

Extracted subqueries use explicit aliases as step names when available: `AS paid`
becomes `steps.paid`. Otherwise generated step names and relation aliases use
`a, b, …, z, aa, ab, …`, skipping collisions. Step references omit redundant
matching aliases; different aliases (including self-joins) remain. Authored step
names and final column names are preserved; temporary `_col_0` names are unchanged.

`--format pretty|compact` and `--enrich true|false` override project settings.
Refresh dbt and catalog artifacts after canonical changes, importing wanted
workbook edits before overwriting the workbook.

### Macros

The current spelling is `preserve_marco` / `--preserve-marco`. Its default `true`
keeps static calls, including wrappers such as `round_money(SUM(amount))` whose
aggregate is visible in the arguments. Macros must expose column dependencies
and cannot hide or change aggregate, window or query semantics. Pass
`--preserve-marco false` to expand static calls before optimization.

Runtime-dependent models are skipped unchanged in either mode, including during
enrichment. The CLI explains the skip:

```text
Skipped gld_customer_attribute_hash: macro 'hash_relation_attributes' requires dbt runtime and cannot be expanded offline. Model unchanged.
```

Other validation or optimization errors still prevent all selected writes.
The [demo gallery](../../examples/jaffle_shop/README.md#4-optimize-the-gold-teaching-inputs)
includes DISTINCT, ranking, correlated subqueries, UNION ALL and column-pruning
cases. Measure physical performance separately on representative warehouse data.
