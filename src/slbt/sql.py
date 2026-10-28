"""Dialect-aware SQL validation and lowering of explicit relation names."""

from __future__ import annotations

from sqlglot import exp, parse
from sqlglot.dialects import Dialect
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from slbt.schema import SpecError, fields, from_dotted, relation, relation_key, text


def sql(node: exp.Expression, dialect="spark", *, pretty=False) -> str:
    try:
        return node.sql(dialect=dialect, pretty=pretty, unsupported_level=ErrorLevel.RAISE)
    except SqlglotError as exc:
        raise SpecError(f"unsupported {dialect} SQL: {exc}") from exc


def quoted(name: str, dialect="spark") -> str:
    return sql(exp.to_identifier(name, quoted=True), dialect)


def minimal_quotes(tree: exp.Expression, dialect="spark") -> exp.Expression:
    """Remove cosmetic identifier quotes without changing dialect name resolution."""
    dialect = Dialect.get_or_raise(dialect)
    keywords = {word.upper() for phrase in dialect.tokenizer_class.KEYWORDS for word in phrase.split()}
    keywords.update(word.upper() for word in dialect.generator_class.RESERVED_KEYWORDS)
    tree = tree.copy()
    for node in tree.find_all(exp.Identifier):
        unquoted = node.copy()
        unquoted.set("quoted", False)
        same_name = dialect.normalize_identifier(node.copy()).name == dialect.normalize_identifier(unquoted).name
        node.set("quoted", not (same_name and exp.SAFE_IDENTIFIER_RE.fullmatch(node.name) and node.name.upper() not in keywords))
    return tree


def parse_one(value: str, context: str, dialect="spark") -> exp.Expression:
    text(value, context)
    if any(marker in value for marker in ("{{", "{%", "{#")):
        raise SpecError(f"{context}: authored Jinja is not allowed in mapping SQL")
    try:
        statements = parse(value, read=dialect, error_level=ErrorLevel.RAISE)
        if len(statements) != 1 or statements[0] is None:
            raise SpecError(f"{context}: expected one SQL statement")
        return statements[0]
    except SqlglotError as exc:
        raise SpecError(f"{context}: invalid {dialect} SQL: {exc}") from exc


def read_query(value: str, context: str, dialect="spark") -> exp.Query:
    tree = parse_one(value, context, dialect)
    if not isinstance(tree, exp.Query):
        raise SpecError(f"{context}: SQL must be one read-only query")
    forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise SpecError(f"{context}: SQL must be one read-only query")
    for node in tree.find_all(exp.SetOperation):
        if len(node.this.selects) != len(node.expression.selects):
            raise SpecError(f"{context}: set-operation branches must have matching column counts")
    return tree


def scalar_tree(value: str, context: str, dialect="spark") -> exp.Expression:
    tree = parse_one(value, context, dialect)
    if isinstance(tree, (exp.Query, exp.DDL, exp.DML, exp.Command, exp.Alias)) or tree.find(exp.Query, exp.Table):
        raise SpecError(f"{context}: expected a scalar SQL expression")
    return tree


def order_tree(value: str, context: str, dialect="spark") -> exp.Ordered:
    """Parse one ordering expression, retaining direction and NULL ordering."""
    text(value, context)
    tree = parse_one("SELECT 1 ORDER BY " + value, context, dialect)
    _options(tree, {"expressions", "order"}, context)
    order = tree.args.get("order")
    if not order or len(order.expressions) != 1:
        raise SpecError(f"{context}: expected one ORDER BY expression")
    _options(order, {"expressions"}, context)
    node = order.expressions[0]
    _options(node, {"this", "desc", "nulls_first"}, context)
    scalar_tree(sql(node.this, dialect), context, dialect)
    return node


def table_relation(table: exp.Table, context: str) -> dict:
    if not all(isinstance(part, exp.Identifier) for part in table.parts):
        raise SpecError(f"{context}: expected an explicit relation name")
    return from_dotted(".".join(part.name for part in table.parts), context)


def relation_tables(tree):
    """Yield physical relation nodes, excluding references to scoped SQL CTEs."""
    for scope in traverse_scope(tree):
        for table in scope.tables:
            if len(table.parts) == 1 and isinstance(scope.sources.get(table.alias_or_name), Scope):
                continue
            yield table


def query_dependencies(tree, context: str) -> list[dict]:
    """Infer stable, deduplicated dependencies from the complete query AST."""
    result = {}
    for table in relation_tables(tree):
        dependency = table_relation(table, context)
        result[relation_key(dependency)] = dependency
    return [result[key] for key in sorted(result)]


def relation_tree(value: dict) -> exp.Table:
    if "source" in value:
        catalog, db, name = "source", *value["source"]
    else:
        db = "step" if "step" in value else "ref"
        catalog, name = None, value[db]
    table = exp.table_(name, db=db, catalog=catalog, quoted=True)
    if "alias" in value:
        table.set("alias", exp.TableAlias(this=exp.to_identifier(value["alias"], quoted=True)))
    return table


def render_relation(value: dict, dialect="spark") -> str:
    if "step" in value:
        result = quoted(value["step"], dialect)
    elif "ref" in value:
        result = "{{ ref('" + value["ref"] + "') }}"
    else:
        result = "{{ source('" + value["source"][0] + "', '" + value["source"][1] + "') }}"
    if "alias" in value:
        result += " AS " + quoted(value["alias"], dialect)
    return result


class Compiler:
    def __init__(self, macros, *, preserve_macro=False):
        self.macros = macros
        self.preserve_macro = preserve_macro
        self.dialect = macros.dialect
        self.tokens: dict[str, str] = {}
        self.runtime_dependencies: dict[str, dict] = {}
        self.runtime_macros: set[str] = set()

    def token(self, value: str) -> exp.Var:
        name = f"__slbt_runtime_{len(self.tokens)}__"
        self.tokens[name] = value
        return exp.Var(this=name)

    def finish(self, tree: exp.Expression, *, pretty=False) -> str:
        result = sql(tree, self.dialect, pretty=pretty)
        for name, value in self.tokens.items():
            result = result.replace(name, value)
        return result

    def expand(self, tree, context):
        if self.preserve_macro:
            return tree
        return self.macros.expand(tree, context, self)

    def scalar(self, value: str, context: str) -> str:
        return self.finish(self.expand(scalar_tree(value, context, self.dialect), context))

    def query(self, value: str, columns: dict, context: str) -> str:
        return self.lower(self.query_tree(value, columns, context), context)

    def query_tree(self, value: str, columns: dict, context: str) -> exp.Query:
        tree = read_query(value, context, self.dialect)
        if columns:
            if any(projection.is_star for query in tree.find_all(exp.Select) for projection in query.selects):
                raise SpecError(f"{context}: wildcard projections are not allowed with declared columns")
            names = [projection.output_name for projection in tree.selects]
            if names != list(columns):
                raise SpecError(f"{context}: SQL projection names and order {names} do not match columns {list(columns)}")
        return self.expand(tree, context)

    def lower(self, tree: exp.Query, context: str, *, pretty=False) -> str:
        replacements = []
        try:
            for table in relation_tables(tree):
                dep = table_relation(table, context)
                replacement = self.token(render_relation(dep, self.dialect))
                alias = table.args.get("alias")
                # Keep runtime Jinja out of the SQL parser and optimizer.
                replacements.append((table, exp.Table(this=replacement, alias=alias)))
            for original, replacement in replacements:
                original.replace(replacement)
            return self.finish(tree, pretty=pretty)
        except SqlglotError as exc:
            raise SpecError(f"{context}: unsupported SQL: {exc}") from exc


def _options(node, allowed, context):
    unsupported = {key for key, value in node.args.items() if value is not None} - set(allowed)
    if unsupported:
        raise SpecError(f"{context}: unsupported structured SQL options {sorted(unsupported)}")


def parse_expression(value: str, context="expression", dialect="spark") -> dict:
    """Catalog FROM text to canonical structured YAML (never discard clauses)."""
    import re

    if not re.match(r"^\s*FROM\b", text(value, context), re.I):
        raise SpecError(f"{context}: structured expression must start with FROM")
    tree = parse_one("SELECT * " + value, context, dialect)
    return structured_query(tree, context, dialect)


def structured_query(tree: exp.Expression, context="expression", dialect="spark", *, pretty=False) -> dict:
    """Extract only clauses which canonical structured transformations can preserve."""
    if not isinstance(tree, exp.Select):
        raise SpecError(f"{context}: expected one structured FROM expression")
    _options(tree, {"expressions", "from_", "joins", "where", "group", "having", "distinct", "order", "limit"}, context)

    def target(table):
        if not isinstance(table, exp.Table):
            raise SpecError(f"{context}: use named relations, not subqueries")
        _options(table, {"this", "db", "catalog", "alias"}, context)
        result = table_relation(table, context)
        if alias := table.args.get("alias"):
            _options(alias, {"this", "quoted"} if isinstance(alias, exp.Identifier) else {"this"}, context)
            result["alias"] = alias.name
        if "source" in result:
            result["source"] = ".".join(result["source"])
        return relation(result, context)

    from_clause = tree.args.get("from_")
    if not from_clause:
        raise SpecError(f"{context}: FROM requires a relation")
    _options(from_clause, {"this"}, context)
    result = {"from": target(from_clause.this)}
    if distinct := tree.args.get("distinct"):
        _options(distinct, set(), context)
        result["distinct"] = True
    for join in tree.args.get("joins", []):
        _options(join, {"this", "side", "kind", "on"}, context)
        kind = join.side.lower() or join.kind.lower() or "inner"
        if join.side and join.kind.lower() not in {"", "outer"}:
            raise SpecError(f"{context}: unsupported join")
        item = {**target(join.this), "type": kind}
        if join.args.get("on") is not None:
            item["on"] = sql(join.args["on"], dialect, pretty=pretty)
        result.setdefault("joins", []).append(item)
    for name in ("where", "having"):
        if clause := tree.args.get(name):
            _options(clause, {"this"}, context)
            result[name] = sql(clause.this, dialect, pretty=pretty)
    if group := tree.args.get("group"):
        _options(group, {"expressions"}, context)
        result["group_by"] = [sql(item, dialect, pretty=pretty) for item in group.expressions]
    if order := tree.args.get("order"):
        _options(order, {"expressions"}, context)
        result["order_by"] = [sql(order_tree(sql(item, dialect), context, dialect), dialect, pretty=pretty) for item in order.expressions]
    if limit := tree.args.get("limit"):
        _options(limit, {"expression"}, context)
        count = limit.expression
        if not isinstance(count, exp.Literal) or not count.is_int or int(count.this) <= 0:
            raise SpecError(f"{context}: structured limit must be a positive integer")
        result["limit"] = int(count.this)
    return result
