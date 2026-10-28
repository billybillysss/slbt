-- A handwritten dbt model consumed by a YAML-defined model.
select customer_id, customer_name
from {{ ref('customer_directory') }}
