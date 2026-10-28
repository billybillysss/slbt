-- Standalone dbt integration fixture.
with
-- Combine matching web and store order columns with UNION ALL.
"combined" as (
    select * from {{ ref("stg_web_orders") }}
    union all
    select * from {{ ref("stg_store_orders") }}
),
"ranked" as (
    select
        o.order_id as "order_id",
        o.customer_id as "customer_id",
        cast(o.ordered_at as date) as "order_date",
        o.amount as "amount",
        row_number() over (
          partition by o.order_id
          order by o.updated_at desc, o.source_system
        ) as "rn"
    from "combined" as "o"
),
-- Keep the latest order version and left join customer names.
"latest" as (
    select
        r.customer_id as "customer_id",
        c.customer_name as "customer_name",
        r.order_date as "order_date",
        r.amount as "amount"
    from "ranked" as "r"
    left join {{ ref("native_customers") }} as "c" on r.customer_id = c.customer_id
    where r.rn = 1
),
-- Keep positive daily totals after deduplication.
"daily" as (
    select
        l.customer_id as "customer_id",
        l.customer_name as "customer_name",
        l.order_date as "order_date",
        count(*) as "order_count",
        sum(l.amount) as "total_amount"
    from "latest" as "l"
    group by l.customer_id, l.customer_name, l.order_date
    having sum(l.amount) > 0
)
select * from "daily"
