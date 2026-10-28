"""Offline projection lineage to direct logical inputs, never guessed runtime SQL."""

import copy

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope, traverse_scope
from sqlglot.schema import MappingSchema

from slbt.schema import dotted
from slbt.sql import parse_one, relation_tables, sql, table_relation


class ColumnLineage:
    def __init__(self, project):
        self.dialect = project.dialect
        self.runtime = {name for name, definition in project.macros.definitions.items() if definition.runtime}
        self.columns = {f"ref.{model.name}": model.output["columns"] for model in project.models}
        self.columns.update({f"source.{identity}": table["columns"] for group in project.sources.values() for identity, table in group["tables"].items()})

    def render(self, tree, columns, model):
        """Keep expressions unqualified; analyze a separate, schema-qualified copy."""
        if not columns:
            return []
        result = [{"name": name, **copy.deepcopy(metadata), "expression": sql(projection.unalias(), self.dialect)} for (name, metadata), projection in zip(columns.items(), tree.selects)]
        query = tree.copy()
        for name, projection in zip(columns, query.selects):
            alias = exp.to_identifier(name, quoted=True)
            if isinstance(projection, exp.Alias):
                projection.set("alias", alias)
            else:
                projection.replace(exp.Alias(this=projection.copy(), alias=alias))
        # A deferred macro may inspect a relation or emit arbitrary SQL. Its
        # arguments are not trustworthy column dependencies, even if parseable.
        for call in list(query.find_all(exp.Anonymous)):
            if call.name.lower() in self.runtime:
                marker = exp.Null()
                marker.meta["slbt_deferred"] = True
                call.replace(marker)

        schema, relations, qualified_names, unknown = MappingSchema(dialect=self.dialect), {}, set(), set()
        inputs = {**self.columns, **{f"step.{name}": value["columns"] for name, value in model.steps.items()}}
        # Logical ref/step names have two parts and sources have three. Flatten
        # them for SQLGlot's uniform-depth schema without changing exported SQL.
        for table in list(relation_tables(query)):
            relation = dotted(table_relation(table, model.name))
            name = f"__slbt_lineage_{len(relations)}"
            relations[name] = relation
            qualified_names.add(tuple(part.name for part in table.parts))
            if not inputs.get(relation):
                unknown.add(name)
            schema.add_table(name, {exp.to_identifier(column, quoted=True): "UNKNOWN" for column in inputs.get(relation, {})})
            alias = table.args.get("alias") or exp.TableAlias(this=table.this.copy())
            table.replace(exp.Table(this=exp.to_identifier(name, quoted=True), alias=alias.copy()))
        for column in query.find_all(exp.Column):
            if tuple(part.name for part in column.parts[:-1]) in qualified_names:
                column.set("db", None)
                column.set("catalog", None)

        try:
            def unknown_columns(source):
                if isinstance(source, exp.Table):
                    return source.name in unknown
                if isinstance(source, Scope):
                    return "*" in source.expression.named_selects and any(unknown_columns(value) for _, value in source.selected_sources.values())
                return True

            # A known input's schema cannot disambiguate a column against an
            # input with no schema. Mask it rather than let qualification infer
            # the known table simply because the other table has no columns.
            for input_scope in traverse_scope(query):
                sources = [source for _, source in input_scope.selected_sources.values()]
                if len(sources) > 1 and any(unknown_columns(source) for source in sources):
                    for column in input_scope.columns:
                        if not column.table:
                            marker = exp.Null()
                            marker.meta["slbt_unresolved"] = sql(column, self.dialect)
                            column.replace(marker)
            query = qualify(query, dialect=self.dialect, schema=schema, validate_qualify_columns=False, identify=False)
            scope = build_scope(query)
        except (SqlglotError, ValueError):
            # Metadata export must still work for SQL the optional offline
            # lineage analyzer cannot qualify (e.g. unsupported table functions).
            for item in result:
                item["lineage"] = {"status": "partial", "upstream_columns": []}
            return result

        for item in result:
            upstream, unresolved, deferred = set(), set(), False
            try:
                graph = lineage(exp.column(item["name"], quoted=True), query, schema=schema, dialect=self.dialect, scope=scope)
                for node in graph.walk():
                    for part in node.expression.walk():
                        deferred |= part.meta.get("slbt_deferred", False)
                        if "slbt_unresolved" in part.meta:
                            unresolved.add(part.meta["slbt_unresolved"])
                    if isinstance(node.expression, exp.Table):
                        relation = relations.get(node.expression.name)
                        column = parse_one(node.name, model.name, self.dialect)
                        if relation is not None and isinstance(column, exp.Column) and not column.is_star:
                            upstream.add((relation, column.name))
                        else:
                            unresolved.add(node.name)
                    elif isinstance(node.expression, exp.Placeholder):
                        unresolved.add(node.name)
                status = "deferred" if deferred else "partial" if unresolved else "resolved"
            except (SqlglotError, ValueError):
                status = "partial"
            item["lineage"] = {"status": status, "upstream_columns": [{"relation": relation, "column": column} for relation, column in sorted(upstream)]}
            if unresolved:
                item["lineage"]["unresolved_columns"] = sorted(unresolved)
        return result
