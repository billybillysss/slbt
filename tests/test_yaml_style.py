import re
from pathlib import Path
from textwrap import dedent

import yaml
import pytest


ROOT = Path(__file__).resolve().parents[1]


def assert_block_style(node):
    if isinstance(node, yaml.MappingNode):
        assert not node.value or not node.flow_style, "Use indented YAML mappings instead of {...}"
        for key, value in node.value:
            assert_block_style(key)
            assert_block_style(value)
    elif isinstance(node, yaml.SequenceNode):
        # Empty collections have no block-style YAML representation.
        assert not node.value or not node.flow_style, "Use YAML '-' sequences instead of [...]"
        for value in node.value:
            assert_block_style(value)


def test_project_yaml_uses_block_style():
    paths = []
    for directory in ("examples", ".github/workflows", "analytics"):
        for path in (ROOT / directory).rglob("*"):
            if path.suffix in {".yml", ".yaml"} and not any(
                part in {"target", "logs", "dbt_packages", "catalog"} for part in path.relative_to(ROOT).parts
            ) and not any(
                path.is_relative_to(ROOT / "examples/jaffle_shop/dbt" / category)
                for category in ("models", "macros", "snapshots")
            ):
                paths.append(path)
    for path in paths:
        assert_block_style(yaml.compose(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "examples").rglob("README.md"))], ids=lambda path: path.relative_to(ROOT).as_posix())
def test_documentation_yaml_uses_block_style(path):
    document = path.read_text(encoding="utf-8")
    examples = re.findall(r"^([ ]*)```yaml\n(.*?)^\1```", document, re.MULTILINE | re.DOTALL)
    if path == ROOT / "README.md":
        assert examples
    for _, example in examples:
        assert_block_style(yaml.compose(dedent(example)))


@pytest.mark.parametrize("document", ["constraints: []", "columns: {}"])
def test_empty_yaml_collections_are_allowed(document):
    assert_block_style(yaml.compose(document))


@pytest.mark.parametrize("document", ["columns: {value: 1}", "group_by: [orders]"])
def test_nonempty_yaml_flow_collections_are_rejected(document):
    with pytest.raises(AssertionError):
        assert_block_style(yaml.compose(document))
