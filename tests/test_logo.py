"""Check SVG accessibility, theme variants and README header references."""

from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"
SVG = "{http://www.w3.org/2000/svg}"
PALETTES = {
    "slbt-logo.svg": {"#0D9488", "#38BDF8", "#0F172A"},
    "slbt-logo-dark.svg": {"#2DD4BF", "#38BDF8", "#F8FAFC"},
    "slbt-mark.svg": {"#0D9488", "#38BDF8"},
    "slbt-mark-dark.svg": {"#2DD4BF", "#38BDF8"},
    "slbt-mark-mono.svg": {"#0F172A"},
}


def geometry(element):
    return (
        element.tag,
        sorted((key, value) for key, value in element.attrib.items() if key not in {"fill", "stroke"}),
        [geometry(child) for child in element],
    )


@pytest.mark.parametrize("name", PALETTES)
def test_svg_is_accessible_self_contained_and_transparent(name):
    root = ET.parse(ASSETS / name).getroot()
    assert root.tag == SVG + "svg"
    assert root.attrib["role"] == "img"
    assert root.attrib["aria-labelledby"].split() == ["title", "description"]
    for tag, identity in (("title", "title"), ("desc", "description")):
        element = root.find(SVG + tag)
        assert element is not None and element.attrib["id"] == identity
        assert element.text and element.text.strip()
    width = "384" if "logo" in name else "128"
    assert root.attrib["viewBox"] == f"0 0 {width} 128"
    assert (root.attrib["width"], root.attrib["height"]) == (width, "128")
    allowed = {SVG + tag for tag in ("svg", "title", "desc", "g", "path", "circle", "rect")}
    colors, identities = set(), []
    for element in root.iter():
        assert element.tag in allowed, f"Unexpected SVG element in {name}: {element.tag}"
        if "id" in element.attrib:
            identities.append(element.attrib["id"])
        for key, value in element.attrib.items():
            assert not key.lower().startswith("on")
            assert key not in {"href", "{http://www.w3.org/1999/xlink}href", "style"}
            assert "url(" not in value.lower()
            if key in {"fill", "stroke"} and value != "none":
                colors.add(value)
    assert len(identities) == len(set(identities))
    assert colors == PALETTES[name]
    # Output tiles are the only rectangles; none is a background canvas.
    assert all(rect.attrib["width"] == rect.attrib["height"] == "12" for rect in root.iter(SVG + "rect"))


def test_symbol_geometry_is_identical_in_all_variants():
    reference = ET.parse(ASSETS / "slbt-mark.svg").getroot().find(f"{SVG}g[@id='semantic-graph']")
    assert reference is not None
    for name in PALETTES:
        symbol = ET.parse(ASSETS / name).getroot().find(f"{SVG}g[@id='semantic-graph']")
        assert symbol is not None
        assert geometry(symbol) == geometry(reference)


def test_wordmark_geometry_is_identical_in_both_themes():
    light = ET.parse(ASSETS / "slbt-logo.svg").getroot().find(f"{SVG}g[@id='wordmark']")
    dark = ET.parse(ASSETS / "slbt-logo-dark.svg").getroot().find(f"{SVG}g[@id='wordmark']")
    assert light is not None and dark is not None
    assert len(light) == 4
    assert geometry(light) == geometry(dark)


class Pictures(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.assets = []

    def handle_starttag(self, tag, attrs):
        if tag == "picture":
            self.inside = True
        elif self.inside and tag in {"source", "img"}:
            self.assets.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "picture":
            self.inside = False


def test_readme_selects_existing_light_and_dark_svg_assets():
    parser = Pictures()
    parser.feed((ROOT / "README.md").read_text(encoding="utf-8"))
    source, image = parser.assets
    assert source == ("source", {
        "media": "(prefers-color-scheme: dark)",
        "srcset": "docs/assets/slbt-logo-dark.svg",
    })
    assert image == ("img", {
        "src": "docs/assets/slbt-logo.svg",
        "alt": "slbt — Semantic Layer Build Tool",
        "width": "384", "height": "128",
    })
    for href in (source[1]["srcset"], image[1]["src"]):
        assert (ROOT / href).is_file()


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.current = (dict(attrs), [])
            self.paragraphs.append(self.current)
        elif self.current is not None:
            self.current[1].append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "p":
            self.current = None


def test_readme_centers_title_logo_badges_and_navigation():
    parser = Paragraphs()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '<h1 align="center">slbt — Semantic Layer Build Tool</h1>' in readme
    parser.feed(readme)
    logo, badges, navigation = parser.paragraphs
    assert logo[0] == badges[0] == navigation[0] == {"align": "center"}
    assert [tag for tag, _ in logo[1]] == ["picture", "source", "img"]
    # Inline anchors/images, without block elements or explicit line breaks.
    assert [tag for tag, _ in badges[1]] == ["a", "img"] * 4
    links = [attrs["href"] for tag, attrs in badges[1] if tag == "a"]
    assert links == [
        "LICENSE", "pyproject.toml",
        "https://github.com/billybillysss/slbt/actions/workflows/ci.yml",
        "docs/index.md",
    ]
    for href in links:
        if not href.startswith("https://"):
            assert (ROOT / href).is_file()
    images = [attrs for tag, attrs in badges[1] if tag == "img"]
    assert [image["alt"] for image in images] == [
        "License: MIT", "Python 3.11+", "CI", "Documentation",
    ]
    assert all(image["src"].startswith("https://") for image in images)
    assert [tag for tag, _ in navigation[1]] == ["a"] * 5
    navigation_links = [attrs["href"] for _, attrs in navigation[1]]
    assert navigation_links == [
        "docs/getting-started.md", "docs/index.md",
        "docs/guides/ai-assisted-workflow.md",
        "examples/jaffle_shop/README.md", "CONTRIBUTING.md",
    ]
    assert all((ROOT / href).is_file() for href in navigation_links)
    # Some previews preserve source newlines; keep navigation on one source line.
    assert any(
        all(f'href="{href}"' in line for href in navigation_links)
        for line in readme.splitlines()
    )
