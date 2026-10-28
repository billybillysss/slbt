-- Reconcile all-status booked payments, not net revenue after refunds.
with checks as (
    select 'customer_count' as check_name,
        (select count(*) from {{ ref('gld_customers') }}) as actual,
        (select count(*) from {{ ref('raw_customers') }}) as expected
    union all
    select 'order_count',
        (select count(*) from {{ ref('gld_orders') }}),
        (select count(*) from {{ ref('raw_orders') }})
    union all
    select 'payment_total',
        (select sum(amount) from {{ ref('gld_orders') }}),
        (select sum(amount) / 100.0 from {{ ref('raw_payments') }})
    union all
    select 'customer_lifetime_value_total',
        (select sum(customer_lifetime_value) from {{ ref('gld_customers') }}),
        (select sum(amount) / 100.0 from {{ ref('raw_payments') }})
    union all
    select 'daily_sales_total',
        (select sum(payment_total) from {{ ref('gld_daily_sales') }}),
        (select sum(amount) / 100.0 from {{ ref('raw_payments') }})
    union all
    select 'payment_method_total',
        (select sum(payment_total) from {{ ref('gld_payment_method_summary') }}),
        (select sum(amount) / 100.0 from {{ ref('raw_payments') }})
)
select * from checks where actual is distinct from expected
