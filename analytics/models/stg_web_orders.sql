select
    cast(order_id as integer) as order_id,
    cast(customer_id as integer) as customer_id,
    ordered_at,
    cast(amount as decimal(12, 2)) as amount,
    updated_at,
    source_system
from {{ ref('channel_orders') }}
where source_system = 'web'
