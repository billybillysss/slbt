# Jaffle Shop: an end-to-end slbt demo

[slbt overview](../../README.md) · [Documentation](../../docs/index.md) ·
[AI-assisted workflow](../../docs/guides/ai-assisted-workflow.md)

The original **dbt Jaffle Shop** customers/orders/payments story, authored as
canonical slbt mappings. The gallery contains **21 models, 3 registered source
tables, 1 customer snapshot**, and bundled dbt Labs seeds. All optimization cases
live physically under **`models/gld/optimization/`** and have `layer: gold`.

Run every command below from the repository root. The dialect is **DuckDB**.
slbt commands are offline; only the explicit dbt command accesses a database.

## 1. Validate, export and build

```sh
uv sync --locked
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run slbt --project-dir examples/jaffle_shop export catalog
uv run dbt build --project-dir examples/jaffle_shop/dbt --profiles-dir examples/jaffle_shop/dbt
```

The local profile creates `jaffle_shop.duckdb` in the working directory. Set
`SLBT_DEMO_DB_PATH` to choose another file. Seeds are loaded into `main_raw`,
models into `main`, and customer history into `snapshots`. No packages, cloud
credentials or runtime downloads are required. The normal build runs 59 nodes:
3 seeds, 21 models, 1 snapshot and 34 data tests.

The `dbt/` folder is one runnable dbt project. Its configuration, profile, seeds
and handwritten tests are version-controlled; only its generated `models/`,
`macros/` and `snapshots/` subfolders are gitignored. Run `slbt export dbt` before
dbt execution to populate those subfolders. Export does not execute dbt, and
`--prune` only cleans generated subfolders, leaving the runnable project files
intact. The repository's handwritten `analytics/` fixture is untouched.

## 2. Follow the business pipeline

```text
dbt seeds → registered sources → Bronze → Silver → Gold

raw_customers → brz_customers → slv_customers ────────────→ gld_customers
                                   └→ customer_history       ↑
raw_orders   → brz_orders    → slv_orders ──→ gld_orders ──────┘
                                                ↑       └→ gld_daily_sales
raw_payments → brz_payments  → slv_payments ──────┘
                                   └→ gld_payment_method_summary
```

| Layer/resource | Capability to show |
| --- | --- |
| [Registered sources](models/brz/sources.yml) | Physical identifiers, typed columns, descriptions, source tests and catalog metadata. |
| `brz_customers`, `brz_orders`, `brz_payments` | Faithful source mappings and lineage. |
| `slv_customers` | Standardized keys/names, tests, model-owned check snapshot. |
| `slv_orders` | Date/key casts, status normalization, relationships and accepted-value tests. |
| `slv_payments` | Scalar macro expansion, cents-to-currency conversion, deliberate missing metadata for enrichment. |
| `gld_orders` | Aggregate payments **before** joining orders, local steps, primary key and enforced contract. |
| `gld_customers` | Left join retains all customers; no-order customers have zero orders/value; contract and tests. |
| `gld_daily_sales` | Grouping, generic composite-key test, column metadata and inherited Gold defaults. |
| `gld_payment_method_summary` | Distinct order counts and explicit view/owner overrides of Gold defaults. |

**Business meaning:** seed payments are cents; Silver converts to currency units.
An order can have several payments (order 25 totals 58.00). Customer lifetime
value includes all order statuses, matching the classic demo's all-status payment
sum. This is **not net revenue**: no refund ledger is supplied. Returned orders
still have payments, and customers with no orders must not disappear.

## 3. Inspect one output across the project

```sh
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers --columns customer_lifetime_value --view plan
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue average_amount
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue --optimize false
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue
```

The [leaderboard](models/gld/optimization/customer_leaderboard.yml) is the main
column-pruning demo. It deduplicates completed positive orders, aggregates by
customer with `HAVING COUNT(*) >= 2`, joins independent activity counts requiring
at least three orders, ranks by payment total/customer ID, then filters, orders
and limits the result.

Inspecting just `ranked_revenue` removes unused averages, largest-order and
returned-order calculations. It retains order ID inside DISTINCT, payment
aggregation, rounding, HAVING, the activity join/filter, window ranking and final
limit. Requesting `average_amount` too retains that calculation in the same query.

Inspection never writes files or executes SQL. Source/ref leaves are logical
names; the output needs physical bindings before execution. The plan is an
offline logical plan, not a warehouse EXPLAIN or a runtime benchmark.

## 4. Optimize the Gold teaching inputs

These files are intentionally **unoptimized**. Preview safely first; keep a copy
if you want to repeat the before/after demonstration after writing changes.

| Model under `models/gld/optimization/` | What optimization must demonstrate |
| --- | --- |
| `gld_optimizer_constants` | Fold a source-free query's constant expressions. |
| `gld_customer_lifetime_value` | Merge pass-through steps, prune unused status and simplify arithmetic. Customers without orders are intentionally absent here, unlike the main customer mart. |
| `gld_distinct_paying_customers` | Preserve DISTINCT customer/order pairs: projecting only customer ID can still yield duplicates. |
| `gld_customer_order_ranking` | Preserve window partitioning, deterministic ties, top-two filter, null ordering and limit. |
| `gld_high_value_customers` | Above-average orders with a credit-card payment: scalar average and correlated EXISTS must retain their filtering semantics. |
| `gld_payment_union` | Preserve duplicate payment rows with UNION ALL. |
| `gld_customer_leaderboard` | Retain DISTINCT, aggregation, HAVING, joins, ranking and limit while pruning unused values. |

```sh
# Preview the entire Gold optimization gallery without changing YAML.
uv run slbt --project-dir examples/jaffle_shop optimize -s gld_optimizer_constants gld_customer_lifetime_value gld_distinct_paying_customers gld_customer_order_ranking gld_high_value_customers gld_payment_union gld_customer_leaderboard --preserve-macro false --dry-run

# Rewrite two easy-to-compare cases, then regenerate integration artifacts.
uv run slbt --project-dir examples/jaffle_shop optimize -s gld_optimizer_constants gld_customer_lifetime_value --preserve-macro false
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run slbt --project-dir examples/jaffle_shop export catalog
```

`--preserve-macro false` expands static macros before optimization.
The default `preserve_macro: true` keeps
static calls, including wrappers around aggregates visible in their arguments.
Preview the array/map demo or the whole project in this mode:

```sh
uv run slbt --project-dir examples/jaffle_shop optimize -s gld_macro_arguments --dry-run
uv run slbt --project-dir examples/jaffle_shop optimize --dry-run
```

Its array contains `coalesce(o.amount, 0)` as a SQL expression, not a quoted SQL
string, so the optimizer can track and rewrite the column dependency safely.
The runtime hash demonstration is skipped unchanged while other models are
optimized. The CLI explains why:

```text
Skipped gld_customer_attribute_hash: macro 'hash_relation_attributes' requires dbt runtime and cannot be expanded offline. Model unchanged.
```

Validation/export support the hash by deferring its expansion to dbt. Other
validation or optimization errors still prevent all selected writes.
Logical optimization is not a promise of faster physical execution.

## 5. Review and edit the catalog

Open **[catalog/catalog.xlsx](catalog/catalog.xlsx)**. It is generated from this
project and intentionally included with the example.

| Sheet | Review/edit |
| --- | --- |
| `sources` | Registered source bindings, source types and descriptions. |
| `brz`, `slv`, `gld` | TABLE → STEP/OUTPUT → COLUMN mappings, expressions, metadata, tests and key/reference summaries. All Gold demos appear on `gld`. |
| `macros` | Scalar/native-test definitions and descriptions. |
| `defaults` | Layer-based materialization and ownership rules. |
| `classifications` | Registered layers/domains and their descriptions. |

Editable inputs are the authored expressions and supported metadata/configuration
cells; header comments describe each field. Key/References must match native
constraints/tests/meta if you edit those definitions.
For a safe first edit, change `Description` on the `customer_lifetime_value`
output-column row of `gld_customers`; leave identities and expressions intact.
Save and close Excel, then preview the import:

```sh
uv run slbt --project-dir examples/jaffle_shop import examples/jaffle_shop/catalog/catalog.xlsx --dry-run

# Apply reviewed edits, then refresh generated artifacts.
uv run slbt --project-dir examples/jaffle_shop import examples/jaffle_shop/catalog/catalog.xlsx
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run slbt --project-dir examples/jaffle_shop export catalog

# Optional editable CSV and resolved, read-only JSON representations.
uv run slbt --project-dir examples/jaffle_shop export catalog --format csv
uv run slbt --project-dir examples/jaffle_shop export catalog --format json
```

An untouched workbook imports as zero changes. Excel formulas/error cells are
not supported: author SQL/JSON as plain cell text. Export can overwrite a catalog,
so import wanted edits first. Default exports do not prune stale files; use
`--prune` only when you intend cleanup in the generated managed scope.

## 6. Enrichment, classifications and runtime capabilities

```sh
uv run slbt --project-dir examples/jaffle_shop enrich -s slv_payments --dry-run
uv run slbt --project-dir examples/jaffle_shop sync-classifications --dry-run
```

`slv_payments.order_id` and `payment_method` deliberately start as shorthand
expressions. Enrichment traces their known Bronze/source metadata and proposes
types/descriptions without overriding authored values. Unresolvable properties
are reported, not guessed. The registry is already synchronized, so classification
sync is normally a no-op; it copies Silver domains into Bronze when needed.

| Model under `models/gld/capabilities/` | Demonstration |
| --- | --- |
| `gld_macro_arguments` | Defaults, positional and named arguments, array/map values and a nested macro. Its fictional one-unit fee is only a macro example, not mart logic. |
| `gld_customer_attribute_hash` | Explicit relation/exclusions; adapter column introspection and hashing deferred to dbt runtime. Offline inspect reports an error; optimize skips the model unchanged with a reason. |
| `gld_customer_cohorts` | Raw CTEs, INTERSECT, EXCEPT and UNION for disjoint customer cohorts. |
| `gld_join_counts` | Structured inner/left/right/full/cross joins consumed by raw UNION ALL. |

The `customer_history` snapshot belongs to `slv_customers` and tracks name changes
with a check strategy. Running `dbt build` again with unchanged seeds adds no new
customer versions. Enforced contracts, native tests and snapshots are executed by
dbt, not slbt. A singular reconciliation test checks customer/order counts and
payment totals across the main marts.

## Data attribution

Seeds come from dbt Labs' [jaffle-shop-classic](https://github.com/dbt-labs/jaffle-shop-classic)
(formerly `dbt-labs/jaffle_shop`), pinned at
`fd7bfacae4f497ff044a6a0275268676bf1b64c3`.
See [seed provenance](dbt/seeds/README.md) and the included
[Apache 2.0 license](dbt/seeds/LICENSE). The slbt mappings are new examples.
