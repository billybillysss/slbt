"""Read-only table inspection: expanded SQL and offline logical plans.

SQL reduction starts outside the complete query, never by deleting authored
outputs. In particular, DISTINCT and set operations can require other outputs
to preserve the requested column's row multiplicity.
"""

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer import optimize
from sqlglot.optimizer.eliminate_ctes import eliminate_ctes
from sqlglot.optimizer.merge_subqueries import merge_subqueries
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.optimizer.simplify import simplify

from slbt.naming import alphabetic_names, name_generated_relations
from slbt.project import transformation_tree
from slbt.schema import SpecError
from slbt.sql import Compiler, minimal_quotes, read_query, sql, table_relation


def target_model(project, name, columns=()):
    model = next((model for model in project.models if model.name == name), None)
    if model is None:
        raise SpecError(f"Unknown model: {name}")
    for column in columns:
        if column not in model.output["columns"]:
            raise SpecError(f"Unknown output column: {name}.{column}")
    return model


def expanded_query(project, name):
    """Expand loaded refs/steps into isolated subqueries; leave external leaves.

    Each insertion keeps its own SQL-local CTE scope, including names that
    coincide with another model's CTEs or authored steps. No generated dbt SQL,
    runtime Jinja, database metadata, or optimizer rewrites enter this tree.
    """
    target_model(project, name)
    models = {model.name: model for model in project.models}
    cache, active = {}, []

    def expand(model_name, step="slbt_output"):
        key = model_name, step
        if key in active:
            chain = " -> ".join(f"{model}.{stage}" for model, stage in [*active, key])
            raise SpecError(f"Cyclic SQL expansion: {chain}")
        if key in cache:
            return cache[key].copy()
        active.append(key)
        try:
            model = models[model_name]
            value = model.output if step == "slbt_output" else model.steps[step]
            compiler = Compiler(project.macros)
            tree = transformation_tree(value, compiler, f"{model_name}.{step}")
            if compiler.tokens:
                raise SpecError(f"{model_name}.{step}: runtime-dependent macros have no offline SQL")
            if any(node.args.get("recursive") for node in tree.find_all(exp.With)):
                raise SpecError(f"{model_name}.{step}: recursive SQL is not supported by inspection")
            # Snapshot scopes before inserting independent upstream query trees.
            for scope in list(traverse_scope(tree)):
                for table in list(scope.tables):
                    if len(table.parts) == 1 and isinstance(scope.sources.get(table.alias_or_name), Scope):
                        continue
                    relation = table_relation(table, "inspection SQL")
                    if "step" in relation:
                        replacement = expand(model_name, relation["step"])
                    elif relation.get("ref") in models:
                        replacement = expand(relation["ref"])
                    else:
                        # Logical namespaces must not be folded by the dialect.
                        for part in table.parts:
                            part.set("quoted", True)
                        continue
                    options = {key for key, value in table.args.items() if value is not None} - {"this", "db", "catalog", "alias"}
                    if options:
                        raise SpecError(f"{model_name}.{step}: cannot expand relation modifiers safely: {', '.join(sorted(options))}")
                    if not table.args.get("alias"):
                        for column in scope.columns:
                            if column.table == table.name and column.db == table.db and column.catalog == table.catalog:
                                column.set("db", None)
                                column.set("catalog", None)
                    alias = table.args.get("alias")
                    if alias is None:
                        alias = exp.TableAlias(this=table.this.copy())
                        alias.meta["slbt_generated"] = True
                    table.replace(exp.Subquery(this=replacement, alias=alias.copy()))
            cache[key] = tree
            return tree.copy()
        finally:
            active.pop()

    return expand(name)


def inspect_query(project, name, columns=None, *, optimized=True):
    """Expand the table, optionally project columns, then safely optimize a copy.

    Do not eliminate joins, unnest predicates, or infer uniqueness from metadata:
    a join can affect row counts without supplying the requested value. Unknown
    schemas/ambiguous columns fail qualification rather than being guessed.
    """
    model = target_model(project, name, columns or ())
    columns = list(dict.fromkeys(columns or model.output["columns"]))
    complete = expanded_query(project, name)
    # Resolve the inner projection's dialect spelling before quoting our wrapper
    # reference: unquoted Snowflake `id`, for example, exposes `ID`, not `id`.
    normalize_identifiers(complete, dialect=project.dialect)
    inner_names = dict(zip(model.output["columns"], (projection.output_name for projection in complete.selects), strict=True))
    # Start with a collision-free wrapper; the shared final naming pass also
    # handles aliases introduced during expansion and SQLGlot qualification.
    wrapper = next(alphabetic_names({*model.steps, *(identifier.name for identifier in complete.find_all(exp.Identifier))}))
    wrapped = exp.select(*(
        exp.alias_(exp.column(inner_names[column], table=wrapper, quoted=True), column, quoted=True)
        for column in columns
    )).from_(
        exp.Subquery(this=complete.copy(), alias=exp.TableAlias(this=exp.to_identifier(wrapper, quoted=True)))
    )
    try:
        result = optimize(
            wrapped,
            dialect=project.dialect,
            rules=(qualify, simplify, pushdown_projections, merge_subqueries, eliminate_ctes),
            infer_schema=True,
            validate_qualify_columns=True,
        ) if optimized else wrapped
        name_generated_relations(result, complete, reserved=model.steps, context=name)
    except SqlglotError as exc:
        action = "optimize" if optimized else "inspect"
        raise SpecError(f"Cannot safely {action} {name}: {exc}") from exc
    if [projection.output_name for projection in result.selects] != columns:
        raise SpecError(f"{name}: optimization changed the output interface")
    return result


def inspect_sql(project, name, columns=None, *, optimized=True):
    rendered = sql(minimal_quotes(inspect_query(project, name, columns, optimized=optimized), project.dialect), project.dialect, pretty=True)
    read_query(rendered, name, project.dialect)
    return rendered + ";"


def inspect_plan(project, name, columns=None, *, optimized=True):
    """Describe every logical query block without executing or replanning it.

    SQLGlot's execution planner can omit clauses such as QUALIFY and overwrite
    nested filters. Render the inspected AST instead: numbered inputs preserve
    all clauses, including CTEs and scalar/correlated subqueries.
    """
    tree = inspect_query(project, name, columns, optimized=optimized)
    def is_block(node):
        return isinstance(node, exp.Query) and not isinstance(node, exp.Subquery)

    blocks = [node for node in tree.walk() if is_block(node)]
    identifiers = {id(node): f"q{index}" for index, node in enumerate(blocks, 1)}
    labels = {"expressions": "Project", "from_": "Input", "joins": "Join", "where": "Filter",
              "group": "Aggregate", "having": "Having", "qualify": "Qualify", "order": "Sort",
              "limit": "Limit", "offset": "Offset", "with_": "CTE", "windows": "Window",
              "this": "Left input", "expression": "Right input"}
    lines = ["Logical plan (query blocks; q1 is the output)"]
    for block in blocks:
        display = block.copy()
        # Replace nested queries with references, retaining their enclosing
        # aliases/CTE definitions. Their complete logic appears in later blocks.
        originals = [node for node in block.walk() if is_block(node)]
        copies = [node for node in display.walk() if is_block(node)]
        for original, copied in zip(originals[1:], copies[1:], strict=True):
            copied.replace(exp.Var(this=identifiers[id(original)]))
        lines.append(f"\n{identifiers[id(block)]}: {block.key.upper()}")
        for key, value in display.args.items():
            if value is None or value == []:
                continue
            values = value if isinstance(value, list) else [value]
            rendered = ", ".join(sql(minimal_quotes(item, project.dialect), project.dialect)
                                 if isinstance(item, exp.Expr) else str(item) for item in values)
            lines.append(f"  {labels.get(key, key.rstrip('_').title())}: {rendered}")
    return "\n".join(lines)
