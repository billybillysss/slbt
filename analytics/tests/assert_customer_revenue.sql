-- A dbt singular test returns rows only when actual and expected results differ.
with expected as (
    select 101 as customer_id, 'Ada' as customer_name, 2 as order_count, 40.00 as revenue
    union all
    select 102, 'Grace', 1, 30.00
), actual as (
    select customer_id, customer_name, order_count, revenue
    from {{ ref('gold_customer_revenue') }}
)
select * from (select * from expected except select * from actual) missing
union all
select * from (select * from actual except select * from expected) unexpected
