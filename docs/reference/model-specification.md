# Canonical model specification

Canonical YAML resource documents have `version: 1`, optional shared `layer` and
`domain`, and `tables` and/or `sources`. Several tables may share one file;
filenames do not determine model identities. Classifications must be registered.

## Structured transformations

This model can be added to the Jaffle Shop project, which supplies `slv_orders`,
the Gold/sales registry and the `nonnegative` dbt test:

```yaml
version: 1
layer: gold
domain: sales
tables:
  - name: customer_order_counts
    description: Number of orders per customer with at least one order.
    output:
      from:
        ref: slv_orders
        alias: o
      group_by:
        - o.customer_id
      columns:
        customer_id:
          expression: o.customer_id
          data_type: bigint
        order_count:
          expression: COUNT(*)
          data_type: bigint
          data_tests:
            - nonnegative
```

A model has optional local `steps` and a required inline `output`. Structured
transformations support `from`, `joins`, `where`, `group_by`, `having`, `distinct`,
`order_by`, `limit` and explicit ordered `columns`.

- Relations specify one of `ref`, `step` or `source`, with an optional alias.
- Joins support `inner`, `left`, `right`, `full` and `cross`; all except cross
  joins require `on`.
- Intermediate ordering requires a limit. Limits are positive integers.
- A shorthand column is a SQL expression. Expanded columns can contain
  `expression`, `description`, `data_type`, `config`, `constraints` and `data_tests`.
- Model descriptions/configuration and final column metadata are exported to dbt.
  Tests and native constraints become dbt properties.
- An enforced model contract requires a type for every final column. Only one
  primary-key declaration is allowed. Adapter support determines enforcement.

See the [customer mart](../../examples/jaffle_shop/models/gld/customers.yml) for a
left join, local aggregation, constraints, descriptions and data tests.

## Raw SQL transformations

Use raw transformations for CTEs, set operations, scalar/correlated subqueries or
other SQL features. This replaces an `output` block, not a complete document:

```yaml
output:
  sql: |
    SELECT o.customer_id, COUNT(*) AS order_count
    FROM ref.slv_orders AS o
    GROUP BY o.customer_id
  columns:
    customer_id:
      data_type: bigint
    order_count:
      data_type: bigint
```

Raw relations use `ref.name`, `step.name` or `source.group.table`. SQL CTE names
remain locally scoped. Raw output columns contain metadata, not expressions, and
must match the SQL projection names and order.

Validation rejects wildcards, DDL/DML, multiple statements, authored Jinja in
transformations, unknown fields, duplicate YAML keys, and unused/cyclic local
steps. Explicit projections keep column dependencies reviewable. Unknown SQL
functions remain the SQL engine's responsibility; validation does not certify
that every function exists on your adapter.

## Sources and snapshots

Registered sources retain database/schema/identifier and column metadata. See
[source definitions](../../examples/jaffle_shop/models/brz/sources.yml).
Model-owned snapshots select explicit output columns with dbt check/timestamp
configuration; see the
[Silver customer model](../../examples/jaffle_shop/models/slv/customers.yml).
slbt exports snapshots; dbt executes and maintains their history.

## Macros

Local scalar macros are called as SQL functions and expanded offline through
sandboxed Jinja with strict undefined handling. Defaults, positional/named
arguments (`=>` or `:=`), arrays and maps are supported. Native
`{% test ... %}` definitions are exported for dbt.

Adapter-dependent macros defer to dbt runtime, for example
`hash_relation_attributes(ref.slv_customers, table_alias => 'c', exclude => array('customer_id'))`.
Validation/export support these calls; inspection rejects them, and optimization
skips affected models unchanged with an explanation. A sandbox is not permission
to run untrusted projects; review macros before dbt execution.

## Enrichment

```sh
uv run slbt --project-dir examples/jaffle_shop enrich -s slv_payments --dry-run
```

Enrichment fills missing types/descriptions from known source/model lineage. It
does not overwrite explicit metadata or query a warehouse. Unresolved properties
are reported rather than guessed. Omit `--dry-run` only after reviewing the
affected files; generated dbt/catalog outputs must then be refreshed.
