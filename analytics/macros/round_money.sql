{% macro round_money(expression) -%}
    cast(round({{ expression }}, 2) as decimal(12, 2))
{%- endmacro %}
