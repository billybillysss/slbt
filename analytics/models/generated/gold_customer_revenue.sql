-- Standalone dbt integration fixture.
with
-- Aggregate completed orders by customer.
"revenue" as (
    select
        o.customer_id as "customer_id",
        o.customer_name as "customer_name",
        count(*) as "order_count",
        {{ round_money('sum(o.amount)') }} as "revenue"
    from {{ ref("silver_orders") }} as "o"
    group by o.customer_id, o.customer_name
)
select * from "revenue"
