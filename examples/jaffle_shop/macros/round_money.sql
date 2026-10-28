{% macro round_money(expression, decimal_places=2) -%}
round({{ expression }}, {{ decimal_places }})
{%- endmacro %}
