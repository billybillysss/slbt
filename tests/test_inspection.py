"""Read-only table inspection preserves projection, query logic and cardinality."""

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlglot import exp

from slbt.cli import cli
from slbt.inspection import inspect_query, inspect_sql, inspect_plan, expanded_query
from slbt.project import Project
from slbt.scaffold import initialize
from slbt.schema import SpecError, dump_yaml, load_yaml
from slbt.sql import read_query


ROOT = Path(__file__).resolve().parents[1]
JAFFLE_SHOP = ROOT / "examples/jaffle_shop"


@pytest.fixture
def root(tmp_path):
    initialize(tmp_path / "project")
    return tmp_path / "project"


def model(name, query, columns=("id",)):
    return {"name": name, "output": {"sql": query, "columns": {name: {} for name in columns}}}


def project(root, models):
    (root / "models/example.yml").write_bytes(dump_yaml({
        "version": 1, "layer": "silver", "domain": "example", "tables": models,
    }))
    return Project(root / "slbt_project.yaml")


def baseline(root):
    # Excel owns and may exclusively lock these transient workbook sidecars.
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and not (path.name.startswith("~$") and path.suffix == ".xlsx")}


def bound_sql(tree):
    """Bind only this test's external orders relation, not production inspection."""
    tree = tree.copy()
    for table in tree.find_all(exp.Table):
        if table.catalog == "source" and table.db == "raw" and table.name == "orders":
            table.set("catalog", None)
            table.set("db", None)
        elif table.catalog == "source" and table.db == "jaffle_shop":
            table.set("catalog", None)
            table.set("db", None)
            table.set("this", exp.to_identifier("raw_" + table.name))
    return tree.sql(dialect="duckdb")


def results(connection, loaded, name, columns, optimized=True):
    columns = [columns] if isinstance(columns, str) else columns
    complete = expanded_query(loaded, name)
    columns = columns or [projection.output_name for projection in complete.selects]
    expected = exp.select(*(exp.column(column, table="target") for column in columns)).from_(complete.subquery("target"))
    actual = inspect_query(loaded, name, columns, optimized=optimized)
    assert [projection.output_name for projection in actual.selects] == columns
    expected_rows = connection.execute(bound_sql(expected)).fetchall()
    actual_rows = connection.execute(bound_sql(actual)).fetchall()
    assert Counter(actual_rows) == Counter(expected_rows)
    return actual_rows


def database(rows):
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect()
    connection.execute("CREATE TABLE orders(order_id BIGINT, customer_id BIGINT, status VARCHAR, amount DOUBLE)")
    if rows:
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", rows)
    return connection


@pytest.mark.parametrize("view", ["sql", "plan"])
@pytest.mark.parametrize("optimized", [True, False])
def test_cli_views_are_read_only_and_valid(root, view, optimized):
    project(root, [model("target", "SELECT o.customer_id AS id, o.order_id AS unused FROM source.raw.orders o", ("id", "unused"))])
    before = baseline(root)
    result = CliRunner().invoke(cli, ["--project-dir", str(root), "inspect", "target", "--columns", "id", "--view", view, "--optimize", str(optimized).lower()])
    assert result.exit_code == 0, result.output
    if view == "sql":
        tree = read_query(result.output, "stdout", "spark")
        assert [projection.output_name for projection in tree.selects] == ["id"]
        assert ("unused" not in result.output) == optimized
        assert "Logical source/ref leaves" in result.output
    else:
        assert "Offline logical plan" in result.output
        assert "Input:" in result.output
    assert baseline(root) == before


@pytest.mark.parametrize("arguments, message", [
    (["inspect", "missing"], "Unknown model"),
    (["inspect", "example", "--columns", "missing"], "Unknown output column"),
    (["inspect", "example", "--view", "mapping"], "Invalid value"),
    (["inspect", "example", "--view", "step"], "Invalid value"),
    (["inspect", "example", "--optimize", "maybe"], "Invalid value"),
    (["inspect", "example", "--optimize"], "requires an argument"),
    (["inspect", "example", "--columns"], "requires an argument"),
])
def test_cli_errors_are_friendly(root, arguments, message):
    result = CliRunner().invoke(cli, ["--project-dir", str(root), *arguments])
    assert result.exit_code != 0
    assert message in result.output
    assert "Traceback" not in result.output


def test_cli_defaults_to_optimized_whole_table_sql(root):
    loaded = project(root, [model("target", "SELECT 1 + 2 AS id, 4 AS marker", ("id", "marker"))])
    before = baseline(root)
    result = CliRunner().invoke(cli, ["--project-dir", str(root), "inspect", "target"])
    assert result.exit_code == 0, result.output
    assert "optimize: true" in result.output
    tree = read_query(result.output, "stdout", loaded.dialect)
    assert [projection.output_name for projection in tree.selects] == ["id", "marker"]
    assert "3 AS id" in result.output
    assert baseline(root) == before


@pytest.mark.parametrize("arguments", [["--columns", "marker", "id"], ["--columns", "marker", "--columns", "id"]])
def test_cli_columns_are_one_query_in_requested_order(root, arguments):
    loaded = project(root, [model("target", "SELECT 1 AS id, 2 AS marker, 3 AS unused", ("id", "marker", "unused"))])
    result = CliRunner().invoke(cli, ["--project-dir", str(root), "inspect", "target", *arguments])
    assert result.exit_code == 0, result.output
    tree = read_query(result.output, "stdout", loaded.dialect)
    assert [projection.output_name for projection in tree.selects] == ["marker", "id"]
    assert "unused" not in result.output


def test_optimization_toggle_preserves_or_merges_expanded_structure(root):
    upstream = model("upstream", "SELECT o.customer_id AS id, o.amount AS unused FROM source.raw.orders o", ("id", "unused"))
    target = model("target", "SELECT s.id, s.unused FROM step.staged s", ("id", "unused"))
    target["steps"] = {"staged": {"sql": "SELECT u.id, u.unused FROM ref.upstream u"}}
    loaded = project(root, [upstream, target])
    original = inspect_query(loaded, "target", ["id"], optimized=False)
    reduced = inspect_query(loaded, "target", ["id"])
    assert len(list(original.find_all(exp.Select))) == 4
    assert len(list(reduced.find_all(exp.Select))) == 1
    for optimized in (False, True):
        rendered = inspect_sql(loaded, "target", ["id"], optimized=optimized)
        assert "ref.upstream" not in rendered
        assert "step.staged" not in rendered
        assert "source.raw.orders" in rendered
    with database([(1, 1, None, 10.0), (2, 1, None, 20.0)]) as connection:
        for optimized in (False, True):
            results(connection, loaded, "target", None, optimized)
            results(connection, loaded, "target", ["unused", "id"], optimized)


@pytest.mark.parametrize("optimized", [False, True])
def test_inspection_uses_alphabetic_wrapper_in_sql_and_plan(root, optimized):
    loaded = project(root, [model("target", "SELECT SUM(o.amount) AS id, COUNT(*) AS unused "
                                  "FROM source.raw.orders AS o GROUP BY o.customer_id", ("id", "unused"))])
    before = baseline(root)
    tree = inspect_query(loaded, "target", ["id"], optimized=optimized)
    assert tree.args["from_"].this.alias == "a"
    assert {alias.name for alias in tree.find_all(exp.TableAlias)} == {"a", "o"}
    rendered = inspect_sql(loaded, "target", ["id"], optimized=optimized)
    plan = inspect_plan(loaded, "target", ["id"], optimized=optimized)
    assert "a.id AS id" in rendered
    assert "Project: a.id AS id" in plan
    assert "Input: FROM (q2) AS a" in plan
    assert "__slbt_target" not in rendered + plan
    assert rendered == inspect_sql(loaded, "target", ["id"], optimized=optimized)
    assert baseline(root) == before


@pytest.mark.parametrize("optimized", [False, True])
def test_inspection_generated_names_skip_authored_aliases_ctes_and_steps(root, optimized):
    target = model("target", "WITH b AS (SELECT s.id, s.amount FROM step.a AS s) "
                            "SELECT SUM(b.amount) AS id FROM b GROUP BY b.id")
    target["steps"] = {"a": {"sql": "SELECT A.customer_id AS id, A.amount "
                                   "FROM source.raw.orders AS A"}}
    loaded = project(root, [target])
    tree = inspect_query(loaded, "target", ["id"], optimized=optimized)
    assert tree.args["from_"].this.alias == "c"
    assert {table.alias.casefold() for table in tree.find_all(exp.Table)} >= {"a"}
    if not optimized:
        assert [cte.alias for cte in tree.find_all(exp.CTE)] == ["b"]
    with database([(1, 1, None, 10.0), (2, 1, None, 20.0)]) as connection:
        assert results(connection, loaded, "target", "id", optimized) == [(30.0,)]


@pytest.mark.parametrize("optimized", [False, True])
def test_inspection_preserves_authored_internal_looking_alias(root, optimized):
    loaded = project(root, [model("target", "SELECT SUM(__slbt_target.amount) AS id "
                                  "FROM source.raw.orders AS __slbt_target GROUP BY __slbt_target.customer_id")])
    tree = inspect_query(loaded, "target", optimized=optimized)
    assert tree.args["from_"].this.alias == "a"
    assert next(tree.find_all(exp.Table)).alias == "__slbt_target"
    with database([(1, 1, None, 10.0), (2, 1, None, 20.0)]) as connection:
        assert results(connection, loaded, "target", "id", optimized) == [(30.0,)]


@pytest.mark.parametrize("optimized", [False, True])
def test_inspection_names_implicit_expansion_aliases_without_changing_references(root, optimized):
    loaded = project(root, [model("upstream", "SELECT orders.customer_id AS id FROM source.raw.orders"),
                            model("target", "SELECT ref.upstream.id AS id FROM ref.upstream")])
    tree = inspect_query(loaded, "target", optimized=optimized)
    aliases = {alias.name for alias in tree.find_all(exp.TableAlias)}
    assert aliases == ({"a"} if optimized else {"a", "b", "c"})
    with database([(1, 1, None, 10.0), (2, 2, None, 20.0)]) as connection:
        assert results(connection, loaded, "target", "id", optimized) == [(1,), (2,)]


@pytest.mark.parametrize("optimized", [False, True])
def test_implicit_alias_matching_an_authored_alias_in_another_scope_is_still_generated(root, optimized):
    loaded = project(root, [model("upstream", "SELECT DISTINCT upstream.customer_id AS id, upstream.order_id AS marker "
                                  "FROM source.raw.orders AS upstream", ("id", "marker")),
                            model("target", "SELECT ref.upstream.id AS id FROM ref.upstream")])
    tree = inspect_query(loaded, "target", optimized=optimized)
    assert next(tree.find_all(exp.Table)).alias == "upstream"
    assert all(query.alias != "upstream" for query in tree.find_all(exp.Subquery))
    with database([(1, 1, None, 10.0), (2, 1, None, 20.0)]) as connection:
        assert results(connection, loaded, "target", "id", optimized) == [(1,), (1,)]


@pytest.mark.parametrize("optimized", [False, True])
def test_inspection_renames_qualified_relation_columns_and_correlated_references(root, optimized):
    loaded = project(root, [model("target", "SELECT source.raw.orders.customer_id AS id, "
                                  "(SELECT MAX(orders.amount) FROM source.raw.orders "
                                  "WHERE orders.customer_id = outer_orders.customer_id) AS total "
                                  "FROM source.raw.orders AS outer_orders "
                                  "JOIN source.raw.orders ON orders.order_id = outer_orders.order_id", ("id", "total"))])
    tree = inspect_query(loaded, "target", ["total", "id"], optimized=optimized)
    assert [projection.output_name for projection in tree.selects] == ["total", "id"]
    assert "outer_orders" in {table.alias for table in tree.find_all(exp.Table)}
    assert all(not column.db and not column.catalog for column in tree.find_all(exp.Column))
    with database([(1, 1, None, 10.0), (2, 1, None, 20.0), (3, 2, None, 40.0)]) as connection:
        assert Counter(connection.execute(bound_sql(tree)).fetchall()) == Counter([(20.0, 1), (20.0, 1), (40.0, 2)])


@pytest.mark.parametrize("optimized", [False, True])
def test_inspection_generated_names_continue_past_z(root, optimized):
    query = " UNION ALL ".join("SELECT orders.customer_id AS id FROM source.raw.orders" for _ in range(28))
    loaded = project(root, [model("target", query)])
    tree = inspect_query(loaded, "target", optimized=optimized)
    aliases = [alias.name for alias in tree.find_all(exp.TableAlias)]
    assert set("abcdefghijklmnopqrstuvwxyz") | {"aa", "ab"} <= set(aliases)
    assert len(aliases) == len(set(aliases))
    assert all(alias.isalpha() and alias.islower() for alias in aliases)
    assert inspect_sql(loaded, "target", optimized=optimized) == inspect_sql(loaded, "target", optimized=optimized)
    with database([(1, 1, None, 10.0)]) as connection:
        assert len(results(connection, loaded, "target", "id", optimized)) == 28


@pytest.mark.parametrize("query, columns", [
    ("SELECT o.customer_id AS id, o.order_id AS unused FROM source.raw.orders o", ("id", "unused")),
    ("SELECT DISTINCT o.customer_id AS id, o.order_id AS marker FROM source.raw.orders o", ("id", "marker")),
    ("SELECT o.customer_id AS id, o.order_id AS marker FROM source.raw.orders o UNION SELECT p.customer_id AS id, p.order_id AS marker FROM source.raw.orders p", ("id", "marker")),
    ("SELECT o.customer_id AS id, o.order_id AS marker FROM source.raw.orders o UNION ALL SELECT p.customer_id AS id, p.order_id AS marker FROM source.raw.orders p", ("id", "marker")),
    ("SELECT o.customer_id AS id, o.order_id AS marker FROM source.raw.orders o INTERSECT SELECT p.customer_id AS id, p.order_id AS marker FROM source.raw.orders p WHERE p.amount > 10", ("id", "marker")),
    ("SELECT o.customer_id AS id, o.order_id AS marker FROM source.raw.orders o EXCEPT SELECT p.customer_id AS id, p.order_id AS marker FROM source.raw.orders p WHERE p.amount > 10", ("id", "marker")),
    ("SELECT SUM(o.amount) AS id, COUNT(*) AS unused FROM source.raw.orders o GROUP BY o.customer_id HAVING COUNT(*) >= 2", ("id", "unused")),
    ("SELECT 42 AS id, COUNT(*) AS unused FROM source.raw.orders o", ("id", "unused")),
    ("SELECT 42 AS id, o.order_id AS unused FROM source.raw.orders o", ("id", "unused")),
    ("SELECT 42 AS id, 7 AS unused", ("id", "unused")),
    ("SELECT o.amount AS id, ROW_NUMBER() OVER (ORDER BY o.amount DESC, o.order_id) AS pos FROM source.raw.orders o QUALIFY pos <= 2", ("id", "pos")),
    ("SELECT o.amount AS id, o.customer_id AS unused FROM source.raw.orders o ORDER BY o.customer_id, o.order_id LIMIT 2 OFFSET 1", ("id", "unused")),
    ("SELECT o.customer_id AS id FROM source.raw.orders o WHERE EXISTS (SELECT p.order_id FROM source.raw.orders p WHERE p.customer_id = o.customer_id AND p.amount > 20)", ("id",)),
    ("SELECT (SELECT MAX(p.amount) FROM source.raw.orders p WHERE p.customer_id = o.customer_id) AS id FROM source.raw.orders o", ("id",)),
])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("projection", ["single", "multiple", "whole"])
def test_sql_matches_complete_projection_including_duplicates_nulls_and_empty_inputs(root, query, columns, empty, optimized, projection):
    loaded = project(root, [model("target", query, columns)])
    rows = [] if empty else [(1, 1, "completed", 10.236), (2, 1, "completed", 20.236), (2, 1, "completed", 20.236), (3, 2, "cancelled", None), (4, None, None, 40.0)]
    with database(rows) as connection:
        selected = ["id"] if projection == "single" else list(reversed(columns)) if projection == "multiple" else None
        results(connection, loaded, "target", selected, optimized)


@pytest.mark.parametrize("optimized", [False, True])
def test_plan_is_deterministic_and_keeps_all_query_clauses(root, optimized):
    query = "WITH staged AS (SELECT o.customer_id AS id, o.amount FROM source.raw.orders o WHERE o.amount > 0) SELECT DISTINCT s.id, ROW_NUMBER() OVER (ORDER BY s.amount) AS pos FROM staged s WHERE s.id > 1 QUALIFY pos <= 2 ORDER BY pos LIMIT 2 OFFSET 1"
    loaded = project(root, [model("target", query, ("id", "pos"))])
    before = baseline(root)
    plan = inspect_plan(loaded, "target", optimized=optimized)
    assert plan == inspect_plan(loaded, "target", optimized=optimized)
    for clause in ("Project:", "Input:", "Filter:", "Qualify:", "Distinct:", "Sort:", "Limit:", "Offset:", "ROW_NUMBER()", "amount > 0", "id > 1", "<= 2"):
        assert clause in plan
    assert baseline(root) == before


def test_complex_plan_keeps_nested_filters_and_grouping():
    loaded = Project(JAFFLE_SHOP / "slbt_project.yaml")
    plan = inspect_plan(loaded, "gld_customer_leaderboard", ["ranked_revenue"])
    for clause in ("all_order_count >= 3", "revenue_rank <= 3", "COUNT(*) >= 2", "Aggregate:", "Having:", "Join:", "Distinct:", "Sort:", "Limit:"):
        assert clause in plan


@pytest.mark.parametrize("join", ["INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"])
def test_joins_that_supply_no_values_still_preserve_row_multiplicity(root, join):
    condition = "" if join == "CROSS JOIN" else " ON a.customer_id = b.customer_id"
    query = f"SELECT a.amount AS id, a.order_id AS unused FROM source.raw.orders a {join} source.raw.orders b{condition}"
    loaded = project(root, [model("target", query, ("id", "unused"))])
    with database([(1, 1, "completed", 10.0), (2, 1, "completed", 20.0), (3, 2, None, 30.0)]) as connection:
        result = results(connection, loaded, "target", "id")
        assert len(result) == (9 if join == "CROSS JOIN" else 5)
    assert "JOIN" in inspect_sql(loaded, "target", ["id"])


def test_step_refs_and_sql_ctes_with_colliding_names_stay_scoped(root):
    upstream = model("upstream", "WITH staged AS (SELECT o.customer_id AS id FROM source.raw.orders o WHERE o.amount > 10) SELECT s.id FROM staged s")
    target = model("target", "WITH staged AS (SELECT p.id FROM step.staged p WHERE p.id > 0) SELECT s.id FROM staged s")
    target["steps"] = {"staged": {"sql": "WITH staged AS (SELECT u.id FROM ref.upstream u) SELECT s.id FROM staged s"}}
    loaded = project(root, [upstream, target])
    with database([(1, 1, None, 10.0), (2, 2, None, 20.0)]) as connection:
        assert results(connection, loaded, "target", "id") == [(2,)]
    assert "ref.upstream" not in inspect_sql(loaded, "target", ["id"])
    assert "step.staged" not in inspect_sql(loaded, "target", ["id"])


def test_fully_qualified_model_column_survives_expansion(root):
    loaded = project(root, [model("upstream", "SELECT 7 AS id"), model("target", "SELECT ref.upstream.id AS id FROM ref.upstream")])
    with database([]) as connection:
        assert results(connection, loaded, "target", "id") == [(7,)]


def test_external_refs_remain_logical_not_invented(root):
    loaded = project(root, [model("target", "SELECT x.id FROM ref.outside x")])
    assert "ref.outside" in inspect_sql(loaded, "target", ["id"])


@pytest.mark.parametrize("dialect", ["spark", "duckdb", "tsql", "postgres", "snowflake"])
def test_output_names_survive_dialect_normalization(root, dialect):
    config = load_yaml(root / "slbt_project.yaml")
    config["optimization"] = {"dialect": dialect}
    (root / "slbt_project.yaml").write_bytes(dump_yaml(config))
    loaded = project(root, [model("upstream", "SELECT 7 AS id"), model("target", "SELECT u.id FROM ref.upstream AS u")])
    tree = read_query(inspect_sql(loaded, "target", ["id"]), "inspection SQL", dialect)
    assert [projection.output_name for projection in tree.selects] == ["id"]
    with database([]) as connection:
        assert connection.execute(tree.sql("duckdb")).fetchall() == [(7,)]


def test_relation_sampling_is_rejected_instead_of_silently_discarded(root):
    config = load_yaml(root / "slbt_project.yaml")
    config["optimization"] = {"dialect": "duckdb"}
    (root / "slbt_project.yaml").write_bytes(dump_yaml(config))
    loaded = project(root, [model("upstream", "SELECT 7 AS id"), model("target", "SELECT u.id FROM ref.upstream AS u TABLESAMPLE (50 PERCENT)")])
    with pytest.raises(SpecError, match="cannot expand relation modifiers safely: sample"):
        inspect_sql(loaded, "target", ["id"])


def test_ambiguous_external_columns_are_not_guessed(root):
    loaded = project(root, [model("target", "SELECT id FROM source.raw.a a JOIN source.raw.b b ON a.id = b.id")])
    with pytest.raises(SpecError, match="Cannot safely optimize.*could not be resolved", check=lambda exc: "id" in str(exc)):
        inspect_sql(loaded, "target", ["id"])


def test_cross_model_cycle_fails_sql_instead_of_recursing(root):
    loaded = project(root, [model("first", "SELECT s.id FROM ref.second s"), model("second", "SELECT f.id FROM ref.first f")])
    with pytest.raises(SpecError, match="Cyclic SQL expansion"):
        inspect_sql(loaded, "first", ["id"])


@pytest.mark.parametrize("render", [inspect_sql, inspect_plan])
@pytest.mark.parametrize("optimized", [False, True])
def test_runtime_sql_is_unavailable_offline(render, optimized):
    loaded = Project(JAFFLE_SHOP / "slbt_project.yaml")
    with pytest.raises(SpecError, match="runtime-dependent macros"):
        render(loaded, "gld_customer_attribute_hash", ["attribute_hash"], optimized=optimized)


def test_complex_demo_prunes_only_unused_values_and_preserves_results():
    loaded = Project(JAFFLE_SHOP / "slbt_project.yaml")
    before = baseline(JAFFLE_SHOP)
    rendered = inspect_sql(loaded, "gld_customer_leaderboard", ["ranked_revenue"])
    assert rendered == inspect_sql(loaded, "gld_customer_leaderboard", ["ranked_revenue"])
    for required in ("DISTINCT", "order_id", "ROUND(CAST(COALESCE(p.amount, 0) AS DECIMAL(12, 2)), 2)", "SUM(c.amount)", "GROUP BY", "HAVING", "COUNT(*) >= 2", "INNER JOIN", "all_order_count >= 3", "ROW_NUMBER() OVER", "revenue_rank <= 3", "ORDER BY", "LIMIT 2"):
        assert required in rendered
    for unused in ("average_amount", "largest_order", "returned_order_count", "MAX(", "NULLIF("):
        assert unused not in rendered
    rows = [
        (1, 1, "completed", 10.24), (2, 1, "completed", 20.24), (2, 1, "completed", 20.24),
        (3, 2, "completed", 80.0), (4, 2, "completed", 30.0), (5, 2, "returned", 1000.0),
        (6, 3, "completed", 50.0), (7, 3, "completed", 40.0), (8, 3, "completed", 20.0),
        (9, 4, "completed", 15.0), (10, 4, "completed", 25.0), (11, 4, "returned", None),
        (12, None, "completed", 99.0), (13, None, "completed", 100.0),
    ]
    duckdb = pytest.importorskip("duckdb")
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE raw_orders(id BIGINT, user_id BIGINT, order_date DATE, status VARCHAR)")
        connection.execute("CREATE TABLE raw_payments(id BIGINT, order_id BIGINT, payment_method VARCHAR, amount BIGINT)")
        connection.execute("CREATE TABLE raw_customers(id BIGINT, first_name VARCHAR, last_name VARCHAR)")
        connection.executemany("INSERT INTO raw_customers VALUES (?, 'Demo', 'Customer')", [(i,) for i in range(1, 5)])
        connection.executemany("INSERT INTO raw_orders VALUES (?, ?, DATE '2018-01-01', ?)", [(i, c, s) for i, c, s, _ in rows])
        # Deliberately duplicate an order row, but not its payment, to exercise DISTINCT.
        payments = {i: amount for i, _, _, amount in rows if amount is not None}
        connection.executemany("INSERT INTO raw_payments VALUES (?, ?, 'credit_card', ?)", [(i, i, round(amount * 100)) for i, amount in payments.items()])
        actual = results(connection, loaded, "gld_customer_leaderboard", "ranked_revenue")
        assert actual == [(110.0,), (110.0,)]
        # A different requested column still retains DISTINCT and per-row rounding.
        assert results(connection, loaded, "gld_customer_leaderboard", "average_amount") == [(55.0,), (36.67,)]
        original = results(connection, loaded, "gld_customers", "customer_lifetime_value")
        assert (Decimal("50.72"),) in original  # non-DISTINCT original retains the duplicate order.
    assert baseline(JAFFLE_SHOP) == before
