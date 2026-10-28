-- Standalone dbt integration fixture.
with
"customers" as (
    select
        c.customer_id as "customer_id",
        c.customer_name as "customer_name"
    from {{ ref("native_customers") }} as "c"
),
"orders" as (
    select
        o.order_id as "order_id",
        o.customer_id as "customer_id",
        o.amount as "amount",
        o.status as "status",
        o.channel as "channel"
    from {{ ref("bronze_orders") }} as "o"
),
-- Join the two CTEs and keep completed orders.
"enriched" as (
    select
        o.order_id as "order_id",
        o.customer_id as "customer_id",
        c.customer_name as "customer_name",
        o.amount as "amount",
        o.channel as "channel"
    from "orders" as "o"
    inner join "customers" as "c" on o.customer_id = c.customer_id
    where o.status = 'completed'
)
select * from "enriched"
