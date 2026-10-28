{% macro hash_relation_attributes(relation, table_alias='', exclude=[]) -%}
  {%- if execute -%}
    {%- set columns = adapter.get_columns_in_relation(relation) -%}
    {%- set selected = [] -%}
    {%- for column in columns | sort(attribute='name') -%}
      {%- if column.name | lower not in exclude | map('lower') | list -%}
        {%- set prefix = adapter.quote(table_alias) ~ '.' if table_alias else '' -%}
        {%- do selected.append('cast(' ~ prefix ~ adapter.quote(column.name) ~ ' as varchar)') -%}
      {%- endif -%}
    {%- endfor -%}
    {{ return("md5(to_json(list_value(" ~ selected | join(', ') ~ ")))" if selected else "md5('[]')") }}
  {%- else -%}
    cast(null as varchar)
  {%- endif -%}
{%- endmacro %}
