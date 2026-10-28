"""Scope-aware SQL relation naming shared by inspection and optimization."""

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from slbt.schema import SpecError, identifier


def alphabetic_names(taken):
    """Allocate query-wide names, reserving even case-insensitive collisions."""
    taken = {name.casefold() for name in taken}
    index = 0
    while True:
        value, name = index + 1, ""
        while value:
            value, digit = divmod(value - 1, 26)
            name = chr(ord("a") + digit) + name
        index += 1
        if name not in taken:
            taken.add(name)
            yield name


def name_generated_relations(tree, original, *, reserved=(), context="SQL"):
    """Rename generated CTEs/aliases by scope, preserving authored names.

    Expansion marks implicit aliases as generated in the original tree so they
    do not accidentally become authored names. SQLGlot name prefixes are never
    used to decide whether a name belongs to the author.
    """
    authored_ctes = {cte.alias.casefold() for cte in original.find_all(exp.CTE)}
    authored_subqueries = {query.alias.casefold() for query in original.find_all(exp.Subquery)
                           if query.alias and not query.args["alias"].meta.get("slbt_generated")}
    authored_aliases = {alias.name.casefold() for alias in original.find_all(exp.TableAlias)
                         if not isinstance(alias.parent, exp.CTE) and not alias.meta.get("slbt_generated")}
    taken = {"slbt_output", *reserved, *authored_ctes, *authored_aliases}
    taken.update(table.name for table in original.find_all(exp.Table))
    taken.update(column.table for column in original.find_all(exp.Column) if column.table)
    names = alphabetic_names(taken)
    scopes = list(traverse_scope(tree))
    steps = {name.casefold() for name in (*reserved, *authored_ctes, "slbt_output")}
    ctes = {}
    for scope in scopes:
        parent = scope.expression.parent
        if isinstance(parent, exp.CTE) and parent.alias.casefold() not in authored_ctes:
            # SQLGlot normally promotes a derived table using its authored alias.
            # Only invent a name when that alias is unavailable or conflicts.
            name = parent.alias.casefold()
            try:
                identifier(name, context)
            except SpecError:
                name = ""
            if name not in authored_subqueries or name in steps:
                name = next(names)
            ctes[id(scope)] = name
            steps.add(name)

    aliases, relations = {}, []
    bindings = {id(scope): scope.selected_sources for scope in scopes}
    for scope in scopes:
        used = set()
        for old, (node, source) in bindings[id(scope)].items():
            relation = node.parent if isinstance(node, exp.Query) and isinstance(node.parent, exp.Subquery) else node
            step = ctes.get(id(source)) if isinstance(node, exp.Table) and len(node.parts) == 1 else None
            alias = old
            parent = source.expression.parent if isinstance(source, Scope) else None
            authored_default = isinstance(parent, exp.CTE) and not step and old == parent.alias
            existing_alias = relation.args.get("alias")
            generated = existing_alias is not None and existing_alias.meta.get("slbt_generated")
            if generated or old.casefold() not in authored_aliases and not authored_default:
                # A generated step and its matching alias share one allocation.
                alias = step if step and step not in used else next(names)
                aliases[id(scope), old] = alias
            used.add(alias)
            relations.append((relation, step, alias))

    # Resolve columns before mutating any names, including correlated references
    # in descendant scopes. A local source shadows an outer source of the same name.
    owners = {id(scope.expression): scope for scope in scopes}
    columns = []
    for column in tree.find_all(exp.Column):
        parent = column.parent
        while parent is not None and id(parent) not in owners:
            parent = parent.parent
        scope = owners.get(id(parent))
        while scope is not None:
            if column.table in bindings[id(scope)]:
                if alias := aliases.get((id(scope), column.table)):
                    columns.append((column, alias))
                break
            scope = scope.parent
    for column, alias in columns:
        column.set("table", exp.to_identifier(alias, quoted=True))
        # A qualified physical/logical relation is now addressed by its alias.
        column.set("db", None)
        column.set("catalog", None)
    for relation, step, alias in relations:
        if step:
            relation.set("this", exp.to_identifier(step, quoted=True))
        if relation.alias_or_name != alias or step and not relation.args.get("alias"):
            existing = relation.args.get("alias")
            replacement = existing.copy() if isinstance(existing, exp.TableAlias) else exp.TableAlias()
            replacement.set("this", exp.to_identifier(alias, quoted=True))
            relation.set("alias", replacement)
    for scope in scopes:
        if name := ctes.get(id(scope)):
            scope.expression.parent.args["alias"].set("this", exp.to_identifier(name, quoted=True))
