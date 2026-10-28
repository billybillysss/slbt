-- Standalone dbt integration fixture.
with
"normalized" as (
    select
        o.customer_id as "customer_id",
        o.channel as "channel",
        o.amount as "amount"
    from {{ ref("silver_orders") }} as "o"
),
"channel_summary" as (
    select
        customer_id,
        sum(case when channel = 'web' then amount else 0 end) as web_sales,
        sum(case when channel = 'store' then amount else 0 end) as store_sales
    from normalized
    group by customer_id
)
select * from "channel_summary"
