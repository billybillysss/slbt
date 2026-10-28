with expected as (
    select 101 as customer_id, 'Ada' as customer_name, date '2026-01-01' as order_date, 2 as order_count, 40.00 as total_amount
    union all
    select 102, 'Grace', date '2026-01-02', 1, 30.00
), actual as (
    select customer_id, customer_name, order_date, order_count, total_amount
    from {{ ref('gold_daily_customer_sales') }}
)
select * from (select * from expected except select * from actual) missing
union all
select * from (select * from actual except select * from expected) unexpected
