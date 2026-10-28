"""Offline documentation contracts: real examples and resolvable local links."""

import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from click.testing import CliRunner
import pytest

from slbt import __version__
from slbt.catalog.export import catalog_outputs
from slbt.cli import cli
from slbt.compiler.dbt import dbt_outputs
from slbt.project import Project
from slbt.schema import load_yaml


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/jaffle_shop"
DOCUMENTS = sorted({
    *ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"),
    *(ROOT / "examples").rglob("README.md"),
})


def blocks(text, language):
    return re.findall(rf"^```{language}\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


def prose(text):
    return re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.MULTILINE | re.DOTALL)


def anchors(text):
    """GitHub-style heading slugs for the headings used in these docs."""
    result, counts = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(text), re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_local_markdown_links(path):
    for href in re.findall(r"\[[^\]\n]+\]\(([^)\s]+)\)", prose(path.read_text(encoding="utf-8"))):
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc:
            continue
        target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
        assert target.is_relative_to(ROOT), f"{path}: link leaves repository: {href}"
        assert target.exists(), f"{path}: missing local link: {href}"
        if parsed.fragment and target.suffix == ".md":
            assert unquote(parsed.fragment) in anchors(target.read_text(encoding="utf-8")), f"{path}: missing anchor: {href}"


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_json_examples_parse(path):
    for block in blocks(path.read_text(encoding="utf-8"), "json"):
        json.loads(block)


def test_readme_examples_match_actual_exports(tmp_path):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = tmp_path / "readme.yml"
    example.write_text(blocks(readme, "yaml")[0], encoding="utf-8")
    assert load_yaml(example) == load_yaml(EXAMPLE / "models/gld/payment_method_summary.yml")
    project = Project(EXAMPLE / "slbt_project.yaml")
    generated = dbt_outputs(project)["models/gld/gld_payment_method_summary.sql"].decode()
    assert blocks(readme, "sql")[0].strip() == generated.strip()
    catalog = json.loads(catalog_outputs(project, "catalog", "json")["catalog.json"])
    excerpt = json.loads(blocks(readme, "json")[0])
    assert {key: value for key, value in excerpt.items() if key != "models"} == {
        key: catalog[key] for key in excerpt if key != "models"
    }
    for item in excerpt["models"]:
        model = next(model for model in catalog["models"] if model["name"] == item["name"])
        assert item == {key: model[key] for key in item}


def test_documented_model_compiles_in_example_project(examples):
    destination = examples
    reference = (ROOT / "docs/reference/model-specification.md").read_text(encoding="utf-8")
    path = destination / "models/gld/customer_order_counts.yml"
    path.write_text(blocks(reference, "yaml")[0], encoding="utf-8")
    project = Project(destination / "slbt_project.yaml")
    assert len(project.models) == 22
    assert "models/gld/customer_order_counts.sql" in dbt_outputs(project)


def test_readme_inspection_example_matches_cli():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Inspect: generate optimized SQL for selected columns\n", 1)[1].split("\n## Optimize:", 1)[0]
    result = CliRunner().invoke(cli, ["--project-dir", str(EXAMPLE), "inspect", "gld_payment_method_summary", "--columns", "payment_total"])
    assert result.exit_code == 0, result.output
    query = "\n".join(line for line in result.output.splitlines() if not line.startswith("--"))
    assert blocks(section, "sql")[0].strip() == query.strip()


def test_readme_optimization_example_matches_yaml_rewrite(tmp_path, examples):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Optimize: simplify and rewrite canonical YAML\n", 1)[1].split("\n## Safety and maturity", 1)[0]
    excerpts = []
    for index, block in enumerate(blocks(section, "yaml")):
        path = tmp_path / f"excerpt-{index}.yml"
        path.write_text(block, encoding="utf-8")
        excerpts.append(load_yaml(path))
    assert len(excerpts) == 2
    destination = examples
    path = destination / "models/gld/optimization/customer_lifetime_value.yml"
    original = load_yaml(path)["tables"][0]
    assert excerpts[0] == {key: original[key] for key in ("steps", "output")}
    arguments = ["--project-dir", str(destination), "optimize", "-s", "gld_customer_lifetime_value", "--preserve-marco", "false"]
    before = {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
    preview = CliRunner().invoke(cli, [*arguments, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()} == before
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    updated = load_yaml(path)["tables"][0]
    assert "steps" not in updated
    assert excerpts[1] == {"output": updated["output"]}
    assert {key: value for key, value in original.items() if key not in {"steps", "output"}} == {
        key: value for key, value in updated.items() if key not in {"steps", "output"}
    }
    assert list(original["output"]["columns"]) == list(updated["output"]["columns"])
    for name, column in original["output"]["columns"].items():
        assert {key: value for key, value in column.items() if key != "expression"} == {
            key: value for key, value in updated["output"]["columns"][name].items() if key != "expression"
        }


def test_cli_version_needs_no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"slbt, version {__version__}"


@pytest.mark.parametrize("arguments", [
    ["validate"],
    ["inspect", "gld_customers", "--columns", "customer_lifetime_value"],
    ["inspect", "gld_payment_method_summary", "--columns", "payment_total"],
    ["inspect", "gld_payment_method_summary", "--columns", "payment_total", "--view", "plan"],
    ["inspect", "gld_payment_method_summary", "--columns", "paid_order_count", "--view", "plan"],
    ["export", "dbt", "--dry-run"],
    ["export", "catalog", "--format", "json", "--dry-run"],
    ["optimize", "-s", "gld_payment_method_summary", "--dry-run"],
    ["optimize", "-s", "gld_customer_lifetime_value", "--preserve-marco", "false", "--dry-run"],
])
def test_documented_preview_commands_succeed_without_writes(arguments):
    before = {path: path.read_bytes() for path in EXAMPLE.rglob("*") if path.is_file() and not any(part in {"dbt", "target", "logs"} for part in path.relative_to(EXAMPLE).parts)}
    result = CliRunner().invoke(cli, ["--project-dir", str(EXAMPLE), *arguments])
    assert result.exit_code == 0, result.output
    after = {path: path.read_bytes() for path in EXAMPLE.rglob("*") if path.is_file() and not any(part in {"dbt", "target", "logs"} for part in path.relative_to(EXAMPLE).parts)}
    assert after == before
