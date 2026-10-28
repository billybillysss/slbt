-- Standalone dbt integration fixture.
with
-- Normalize the physical source into stable SQL types.
"typed" as (
    select
        cast(o.order_id as integer) as "order_id",
        cast(o.customer_id as integer) as "customer_id",
        cast(o.amount as decimal(12, 2)) as "amount",
        o.status as "status",
        case when o.order_id % 2 = 1 then 'web' else 'store' end as "channel"
    from {{ source("raw", "orders") }} as "o"
)
select * from "typed"
