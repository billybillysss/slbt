# Original Jaffle Shop seeds

These three CSV files are copied from dbt Labs' classic Jaffle Shop, pinned to
commit [`fd7bfacae4f497ff044a6a0275268676bf1b64c3`](https://github.com/dbt-labs/jaffle-shop-classic/tree/fd7bfacae4f497ff044a6a0275268676bf1b64c3/seeds).
The repository was formerly named `dbt-labs/jaffle_shop`.

| File | Rows | Grain |
| --- | ---: | --- |
| raw_customers.csv | 100 | Customer |
| raw_orders.csv | 99 | Order |
| raw_payments.csv | 113 | Payment transaction |

Content is unchanged; line endings are normalized. The original Apache 2.0
license is included in [LICENSE](LICENSE). No runtime download is needed.

Payment amounts are **cents**. Orders can have multiple payments. The supplied
data has no refund ledger: summing payments across all order statuses is the
classic demo's lifetime value, not net recognized revenue. The canonical slbt
mappings and DuckDB harness are new demonstrations, not upstream dbt models.
