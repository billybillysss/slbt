{% macro rounded_total(expressions, options={}) -%}
{{ round_money(expressions | join(' + '), options.get('decimal_places', 2)) }}
{%- endmacro %}
