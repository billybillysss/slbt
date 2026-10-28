with expected as (
    select 101 as customer_id, 25.50 as web_sales, 14.50 as store_sales
    union all
    select 102, 0.00, 30.00
), actual as (
    select customer_id, web_sales, store_sales from {{ ref('customer_summary') }}
)
select * from (select * from expected except select * from actual) missing
union all
select * from (select * from actual except select * from expected) unexpected
