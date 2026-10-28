"""Editable XLSX/CSV mapping catalogs and validate-before-write imports."""

from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
import pathspec

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from slbt.macros import BLOCK
from slbt.paths import configured_path, safe_path, validate_destinations
from slbt.schema import SpecError, compact_columns, dotted, dump_yaml, fields, identifier, mapping, text
from slbt.sql import order_tree, parse_expression, read_query, scalar_tree, sql
from slbt.definitions import defaults, classifications, document
from slbt.definitions.source import properties as source_properties, GROUP, TABLE, COLUMN


HEADERS = ["Type", "Name", "Version", "Layer", "Domain", "Distinct", "Key", "References", "Snapshot", "Expression", "Data Type", "Description", "Config", "Constraints", "Tests", "Meta"]
SOURCE_HEADERS = ["Type", "Name", "Layer", "Domain", "Database", "Schema", "Identifier", "Loader", "Data Type", "Source Data Type", "Description", "Config", "Tests", "Meta", "Properties"]
MACRO_HEADERS = ["Path", "Name", "Expression", "Description"]
DEFAULT_HEADERS = ["Name", "Config", "Target", "Type"]
CLASSIFICATION_HEADERS = ["Layer", "Domain", "Description"]


@dataclass
class Catalog:
    name: str
    sheets: dict
    macro_sheets: list[str]
    defaults_sheet: str | None
    macro_files: set[Path]
    macro_selections: list[str]
    source_sheets: dict
    format: str = "xlsx"


def safe_name(value, context, *, sheet=False):
    text(value, context)
    if value.strip() != value or value in {".", ".."} or any(c in value for c in '/\\:*?[]') or any(ord(c) < 32 for c in value) or sheet and (len(value) > 31 or value.startswith("'") or value.endswith("'") or value.casefold() == "history"):
        raise SpecError(f"{context}: invalid {'sheet' if sheet else 'catalog'} name")
    if not sheet and (any(c in value for c in '<>"|') or value.endswith((".", " ")) or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise SpecError(f"{context}: filename is not portable")
    return value


def selection_values(value, context):
    if not isinstance(value, list):
        raise SpecError(f"{context}: file selections must be lists")
    for pattern in value:
        text(pattern, context)
        if "\\" in pattern or Path(pattern).is_absolute() or ":" in pattern or ".." in pattern.split("/"):
            raise SpecError(f"{context}: selections must stay within the input root")
    return value


def selected(path: Path, root: Path, patterns: list[str]) -> bool:
    relative = path.relative_to(root).as_posix()
    for pattern in patterns:
        pattern = pattern.removeprefix("./")
        if pattern in {"", "."}:
            return True
        if relative == pattern.rstrip("/") or relative.startswith(pattern.rstrip("/") + "/"):
            return True
        if pathspec.PathSpec.from_lines("gitignore", [pattern]).match_file(relative):
            return True
    return False


def matches(actual, pattern):
    return defaults.matches(actual, pattern)


def definitions(project) -> list[Catalog]:
    configured = project.authored["catalog"]
    if not isinstance(configured, list) or not configured:
        raise SpecError("catalog must be a nonempty list")
    result, names = [], set()
    macro_files = {p for p in project.macro_root.rglob("*") if p.is_file() and p.suffix in {".sql", ".yml", ".yaml"}}
    for value in configured:
        mapping(value, "catalog")
        fields(value, {"name", "include", "sheets", "macros", "format"}, {"name"}, "catalog")
        name = safe_name(value["name"], "catalog name")
        if Path(name).suffix:
            raise SpecError("catalog names must be extensionless")
        format = value.get("format", "xlsx")
        if format not in {"xlsx", "csv", "json"}:
            raise SpecError("catalog format must be xlsx, csv, or json")
        if name.casefold() in names:
            raise SpecError(f"duplicate catalog name: {name}")
        names.add(name.casefold())
        include = mapping(value.get("include", {}), name)
        fields(include, {"models", "sources", "macros"}, set(), name)
        model_patterns = selection_values(include.get("models", ["./"]), name)
        macro_patterns = selection_values(include.get("macros", ["./"]), name)
        source_patterns = selection_values(include.get("sources", ["./"]), name)
        for category, patterns, paths, root in (("models", model_patterns, [m.path for m in project.models], project.model_root), ("sources", source_patterns, list(project.source_paths.values()), project.model_root), ("macros", macro_patterns, list(macro_files), project.macro_root)):
            if category in include:
                for pattern in patterns:
                    if not any(selected(p, root, [pattern]) for p in paths):
                        raise SpecError(f"{name}: unmatched {category} selection: {pattern}")
        candidates = [model for model in project.models if selected(model.path, project.model_root, model_patterns)]
        sheet_values = value.get("sheets", [{"name": "Models", "type": "model"}])
        if not isinstance(sheet_values, list):
            raise SpecError(f"{name}: sheets must be a list")
        sheets, source_sheets, assigned = {}, {}, set()
        used_names = {"classifications"}

        def sheet_name(value):
            value = safe_name(value, name, sheet=True)
            if value.casefold() in used_names:
                raise SpecError(f"{name}: duplicate sheet name {value}")
            used_names.add(value.casefold())
            return value

        for sheet in sheet_values:
            mapping(sheet, name)
            fields(sheet, {"name", "type", "includes"}, {"name", "type"}, name)
            if sheet["type"] not in {"model", "source"}:
                raise SpecError("sheet type must be model or source")
            title = sheet_name(sheet["name"])
            filters = mapping(sheet.get("includes", {}), title)
            fields(filters, {"layer", "domain", "tables"}, set(), title)
            for key, patterns in filters.items():
                if key == "tables":
                    patterns = patterns if isinstance(patterns, list) else [patterns]
                    if not patterns:
                        raise SpecError(f"{title}: tables must be nonempty")
                else:
                    patterns = [patterns]
                for pattern in patterns:
                    text(pattern, title)
                    # Validate regex even if there are no candidate models.
                    matches("", pattern)
            def included(identity, data):
                return all(any(matches(identity if key == "tables" else data.get(key), p) for p in (patterns if isinstance(patterns, list) else [patterns])) for key, patterns in filters.items())
            if sheet["type"] == "model":
                models = [model for model in candidates if included(model.name, model.authored)]
                identities = {("model", model.name) for model in models}
                sheets[title] = models
            else:
                tables = {identity: table for group in project.sources.values() for identity, table in group["tables"].items() if selected(project.source_paths[identity], project.model_root, source_patterns) and included(identity, table)}
                identities = {("source", identity) for identity in tables}
                source_sheets[title] = tables
            if assigned & identities:
                raise SpecError(f"{name}: a table may appear on at most one sheet")
            assigned.update(identities)
        macros = value.get("macros", [])
        if not isinstance(macros, list):
            raise SpecError(f"{name}: macros must be a list of named sheets")
        if len(macros) > 1:
            raise SpecError("at most one macro sheet is supported")
        macro_sheets = []
        for macro in macros:
            mapping(macro, name)
            fields(macro, {"name"}, {"name"}, name)
            macro_sheets.append(sheet_name(macro["name"]))
        defaults_sheet = sheet_name(project.default_configs.get("name", "Defaults")) if project.default_configs is not None else None
        result.append(Catalog(name, sheets, macro_sheets, defaults_sheet, {p for p in macro_files if selected(p, project.macro_root, macro_patterns)}, macro_patterns, source_sheets, format))
    csv_names = set()
    for catalog in result:
        if catalog.format == "csv":
            for title in [*catalog.sheets, *catalog.source_sheets]:
                safe_name(title, "CSV filename")
                if title.casefold() in csv_names:
                    raise SpecError("CSV sheet filenames collide across catalogs")
                csv_names.add(title.casefold())
    return result


def choose(project, names=None) -> list[Catalog]:
    catalogs = definitions(project)
    if not names:
        return catalogs
    by_name = {catalog.name: catalog for catalog in catalogs}
    if set(names) - by_name.keys():
        raise SpecError(f"unknown catalogs: {sorted(set(names) - by_name.keys())}")
    return [by_name[name] for name in dict.fromkeys(names)]


def json_cell(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else ""


def config_cells(config):
    config = copy.deepcopy(config)
    meta = config.pop("meta", None)
    return json_cell(config) if config else "", json_cell(meta)


def compact_expression(value, dialect="spark", *, query=False, ordered=False):
    """Compact SQL syntax, never whitespace inside literals or comments."""
    if not value or not any(char in value for char in "\r\n"):
        return value
    parser = read_query if query else order_tree if ordered else scalar_tree
    return sql(parser(value, "catalog Expression", dialect), dialect)


def structured_expression(value, dialect="spark"):
    def target(relation):
        return dotted(relation) + (" AS " + relation["alias"] if "alias" in relation else "")

    result = "FROM " + target(value["from"])
    for join in value.get("joins", []):
        result += " " + join["type"].upper() + " JOIN " + target(join)
        if "on" in join:
            result += " ON " + compact_expression(join["on"], dialect)
    if "where" in value:
        result += " WHERE " + compact_expression(value["where"], dialect)
    if value.get("group_by"):
        result += " GROUP BY " + ", ".join(compact_expression(item, dialect) for item in value["group_by"])
    if "having" in value:
        result += " HAVING " + compact_expression(value["having"], dialect)
    if value.get("order_by"):
        result += " ORDER BY " + ", ".join(compact_expression(item, dialect, ordered=True) for item in value["order_by"])
    if "limit" in value:
        # Catalog FROM fragments use canonical LIMIT even for dialects whose
        # executable SELECT renders it as TOP (SQLGlot parses both forms).
        result += " LIMIT " + str(value["limit"])
    return result


def model_rows(model):
    data = model.authored
    config = copy.deepcopy(model.config)
    if "domain" in data and "meta" in config:
        config["meta"].pop("domain", None)
        if not config["meta"] and "meta" not in data.get("config", {}):
            config.pop("meta")
    config_text, meta = config_cells(config)
    if data.get("config") == {}:
        config_text = "{}"
    rows = [["TABLE", model.name, "1", data["layer"], data.get("domain", ""), "", "", "", "", "", "", data.get("description", ""), config_text, json_cell(data.get("constraints")), json_cell(data.get("data_tests")), meta]]
    from slbt.catalog.keys import summaries
    keys = summaries({**data, "output": model.output})
    if model.snapshot:
        conf, smeta = config_cells(model.snapshot["config"])
        rows.append(["SNAPSHOT", model.snapshot["name"], "", "", "", "", "", "", "", "", "", "", conf or "{}", "", "", smeta])
    for name, value in [*model.steps.items(), (model.name, model.output)]:
        authored = data["output"] if name == model.name and value is model.output else data.get("steps", {}).get(name, value)
        expression = compact_expression(value["sql"], model.dialect, query=True) if "sql" in value else structured_expression(value, model.dialect)
        rows.append(["OUTPUT" if value is model.output else "STEP", name, "", "", "", str(authored["distinct"]).lower() if "distinct" in authored else "", "", "", "", expression, "", value.get("description", ""), "", "", "", ""])
        for column_name, column in value["columns"].items():
            conf, meta = config_cells(column.get("config", {}))
            if column.get("config") == {}:
                conf = "{}"
            key, references = keys[column_name] if value is model.output else ("", "")
            indicator = str(column_name in model.snapshot["columns"]).lower() if value is model.output and model.snapshot else ""
            rows.append(["COLUMN", column_name, "", "", "", "", key, references, indicator, compact_expression(column.get("expression", ""), model.dialect), column.get("data_type") or "", column.get("description") or "", conf, json_cell(column.get("constraints")), json_cell(column.get("data_tests")), meta])
    return rows


def macro_folder(path, root):
    try:
        relative = Path(os.path.relpath(path.parent, root)).as_posix()
    except ValueError as exc:
        raise SpecError("macro folders must share the project drive for relative catalog paths") from exc
    return "./" if relative == "." else relative + "/"


def macro_rows(project, catalog):
    rows = []
    previous = None
    for key, definition in sorted(project.macros.definitions.items(), key=lambda pair: (pair[1].path.parent, pair[1].path.name, pair[0])):
        if definition.path in catalog.macro_files:
            folder = macro_folder(definition.path, project.path.parent)
            if folder != previous:
                rows.append([folder, "", "", ""])
                previous = folder
            rows.append(["", definition.name, definition.expression, project.macros.descriptions.get(key, "")])
    return rows


def catalog_rows(project, catalog, *, excel=True):
    result = {title: (HEADERS, [row for model in models for row in model_rows(model)]) for title, models in catalog.sheets.items()}
    for title, tables in catalog.source_sheets.items():
        result[title] = (SOURCE_HEADERS, source_rows(project, tables))
    if excel:
        for title in catalog.macro_sheets:
            result[title] = (MACRO_HEADERS, macro_rows(project, catalog))
    if excel and catalog.defaults_sheet:
        rows = [[rule["name"], json_cell(rule.get("config", {})), json_cell(rule["target"]), group] for group in ("configs", "snapshot_configs") for rule in project.default_configs.get(group, [])]
        result[catalog.defaults_sheet] = (DEFAULT_HEADERS, rows)
    if excel:
        rows = []
        for layer in project.classifications["layers"]:
            rows.append([layer["name"], "", layer["description"]])
            rows.extend(["", domain["name"], domain["description"]] for domain in layer["domains"])
        result["classifications"] = (CLASSIFICATION_HEADERS, rows)
    return result


def source_rows(project, tables):
    rows = []
    for name, group in project.sources.items():
        selected_tables = {identity: table for identity, table in tables.items() if identity in group["tables"]}
        if not selected_tables:
            continue
        rows.append(source_row("SOURCE", name, group))
        for identity, table in selected_tables.items():
            rows.append(source_row("TABLE", identity, table))
            rows.extend(source_row("COLUMN", n, c) for n, c in table["columns"].items())
    return rows


def source_row(kind, name, value):
    config, meta = config_cells(value.get("config", {}))
    if value.get("config") == {}:
        config = "{}"
    extra = {k: value[k] for k in ("quoting",) if k in value} if kind == "SOURCE" else {k: value[k] for k in (("constraints", "docs", "quoting", "external") if kind == "TABLE" else ("constraints", "docs", "quote")) if k in value}
    return [kind, name, value.get("layer", "") if kind == "TABLE" else "", value.get("domain", "") if kind == "TABLE" else "", value.get("database", "") if kind == "SOURCE" else "", value.get("schema", "") if kind == "SOURCE" else "", value.get("identifier", "") if kind == "TABLE" else "", value.get("loader", "") if kind == "SOURCE" else "", value.get("data_type", "") if kind != "SOURCE" else "", value.get("source_data_type", "") if kind == "COLUMN" else "", value.get("description", ""), config, json_cell(value.get("data_tests")) if kind != "SOURCE" else "", meta, json_cell(extra) if extra else ""]


def workbook_bytes(sheets):
    book = Workbook()
    book.remove(book.active)
    widths = {
        "Type": 14, "Name": 32, "name": 28, "Path": 30, "Version": 10, "Layer": 14,
        "Domain": 18, "Dependencies": 30, "Expression": 80, "Data type": 22,
        "Description": 48, "Config": 45, "config": 50, "Constraints": 38,
        "Tests": 34, "Meta": 40, "Distinct": 12, "apply to": 45,
    }
    for title, (headers, rows) in sheets.items():
        sheet = book.create_sheet(title)
        models = headers == HEADERS
        macros = headers == MACRO_HEADERS
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.outlinePr.summaryBelow = False
        sheet.sheet_properties.outlinePr.showOutlineSymbols = True
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.sheet_properties.tabColor = "24465A" if models else "357568" if macros else "7B6B46"
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = "1:1"
        previous_folder, folder_band, parent_kind = None, False, None
        for index, row in enumerate([headers, *rows], 1):
            kind = row[0] if models and index > 1 else None
            if kind in {"TABLE", "STEP", "OUTPUT"}:
                parent_kind = kind
            starts_folder = macros and index > 1 and row[0] != previous_folder
            if starts_folder:
                previous_folder, folder_band = row[0], not folder_band
            level = {"TABLE": 0, "STEP": 1, "OUTPUT": 1, "COLUMN": 2}.get(kind, 0)
            sheet.row_dimensions[index].outlineLevel = level
            sheet.row_dimensions[index].height = 15
            for column, value in enumerate(row, 1):
                if isinstance(value, str) and len(value) > 32767:
                    raise SpecError(f"{title}: Excel cell exceeds 32,767 characters; use CSV")
                value = str(value) if value is not None else ""
                cell = sheet.cell(index, column, value if value != "" else None)
                if isinstance(value, str):
                    cell.data_type = "s"
                cell.font = Font(name="Arial", size=10, color="243746")
                cell.alignment = Alignment(vertical="top", wrap_text=True, indent=level if models and column == 2 else 0)
                if index == 1:
                    cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="36586B")
                    underline = "DDE7ED"
                elif kind == "TABLE":
                    cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="24465A")
                    cell.border = Border(top=Side(style="medium", color="FFFFFF"))
                    underline = "D5E2E9"
                elif kind in {"STEP", "OUTPUT"}:
                    cell.font = Font(name="Arial", size=10, bold=True, color="24465A")
                    cell.fill = PatternFill("solid", fgColor="E7EFF6" if kind == "STEP" else "E3F1EC")
                    cell.border = Border(top=Side(style="thin", color="DBE7F0" if kind == "STEP" else "D6EADF"))
                    underline = "F0F5FA" if kind == "STEP" else "EFF8F3"
                elif kind == "COLUMN":
                    cell.fill = PatternFill("solid", fgColor="F5FAF7" if parent_kind == "OUTPUT" else "FFFFFF")
                    underline = "E8F3ED" if parent_kind == "OUTPUT" else "EFF4F9"
                else:
                    cell.fill = PatternFill("solid", fgColor="F2F6F8" if (folder_band if macros else index % 2 == 0) else "FFFFFF")
                    underline = "EAF2EF" if macros else "EDF1F4"
                    if starts_folder:
                        cell.border = Border(top=Side(style="thin", color="B4C9D3"))
                    if macros and column in {1, 2}:
                        cell.font = Font(name="Arial", size=10, bold=True, color="357568" if column == 1 else "243746")
                cell.border = Border(top=cell.border.top, bottom=Side(style="thin", color=underline))
                if index > 1 and isinstance(value, str):
                    # Keep long SQL readable without making entire sheets unmanageable.
                    width = max(1, int(widths.get(headers[column - 1], 22)) - 2)
                    lines = sum(max(1, (len(line) + width - 1) // width) for line in value.split("\n"))
                    sheet.row_dimensions[index].height = max(sheet.row_dimensions[index].height, min(150, 15 * lines))
        if models:
            sheet.sheet_format.outlineLevelRow = 2
            sheet["G1"].comment = Comment("Key and References summarize native constraints/tests/meta; update these cells to match native metadata edits.", "slbt")
            sheet["A1"].comment = Comment(
                "Hierarchy: TABLE > STEP/OUTPUT > COLUMN. Use the outline controls to collapse columns or transformations. "
                "Output columns use a very pale teal fill; step columns are white. Each row has a subtle color-matched underline. "
                "Edit expressions, descriptions, and metadata in their existing rows; keep every TABLE and its final OUTPUT. "
                "Distinct belongs to structured STEP/OUTPUT rows: blank omits the YAML key (default false), TRUE/FALSE writes it explicitly. "
                "Config/Meta are JSON objects; Constraints/Tests are JSON arrays. Do not enter Excel formulas.", "slbt")
        elif macros:
            sheet["A1"].comment = Comment(
                "Path is the SQL file's parent folder relative to the project folder: macros/ or macros/utils/, for example. "
                "Existing definitions keep their SQL filenames on import. New definitions use <Path>/<Name>.sql. "
                "Edit complete macro/test definitions in Expression and descriptions in Description; do not rename or move existing definitions.", "slbt")
        else:
            sheet["A1"].comment = Comment(
                "Edit defaults using JSON objects in config and apply to. Existing rows are examples of the required format. "
                "These rules affect all matching models; do not enter Excel formulas.", "slbt")
        sheet.freeze_panes = "C2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            header = column[0].value
            sheet.column_dimensions[column[0].column_letter].width = widths.get(header, 22)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def export_catalog(project, *, names=None, format=None, path=None, dry_run=False, prune=False):
    from slbt.catalog.export import catalog_outputs
    from slbt.export import reconcile
    catalogs = choose(project, names)
    if path is not None and len(catalogs) != 1:
        raise SpecError("a catalog path override requires exactly one selected catalog")
    files, formats = {}, set()
    for catalog in catalogs:
        effective = format or catalog.format
        formats.add(effective)
        for relative, content in catalog_outputs(project, catalog, effective).items():
            if relative.casefold() in {p.casefold() for p in files}:
                raise SpecError(f"catalog output collision: {relative}")
            files[relative] = content
    return reconcile(project.destination("catalog", path), "catalog", files, dry_run, prune=prune and not names, formats=formats)


def read_catalog(path: Path, expected):
    safe_path(path, tree=path.is_dir())
    result = {}
    if path.suffix.lower() == ".xlsx" and path.is_file():
        try:
            book = load_workbook(path, data_only=False)
            try:
                if set(book.sheetnames) != set(expected):
                    raise SpecError("workbook sheets do not match the current catalog definition")
                for sheet in book:
                    rows = []
                    for row in sheet.iter_rows():
                        values = []
                        for cell in row:
                            if cell.data_type in {"f", "e"}:
                                raise SpecError(f"{sheet.title}!{cell.coordinate}: Excel formulas and errors are not allowed")
                            if cell.value is not None and not isinstance(cell.value, str):
                                raise SpecError(f"{sheet.title}!{cell.coordinate}: editable cells must contain text")
                            if isinstance(cell.value, str) and len(cell.value) > 32767:
                                raise SpecError("Excel cell exceeds 32,767 characters")
                            values.append(cell.value if cell.value is not None else "")
                        rows.append(values)
                    result[sheet.title] = rows
            finally:
                book.close()
        except (OSError, ValueError, KeyError) as exc:
            if isinstance(exc, SpecError):
                raise
            raise SpecError(f"cannot read workbook: {exc}") from exc
    else:
        filenames = {title + ".csv" for title in expected}
        if path.is_dir():
            actual = {p.name for p in path.iterdir()}
            if actual != filenames:
                raise SpecError("CSV directory must contain exactly the configured CSV filenames")
            files = {title: path / (title + ".csv") for title in expected}
        elif path.is_file() and path.suffix.lower() == ".csv" and len(expected) == 1:
            files = {next(iter(expected)): path}
        else:
            raise SpecError("import requires an XLSX workbook, a complete CSV directory, or a single-sheet CSV file")
        for title, file in files.items():
            with file.open(encoding="utf-8-sig", newline="") as stream:
                result[title] = list(csv.reader(stream))
    normalized = {}
    for title, rows in result.items():
        headers = expected[title][0]
        if not rows:
            raise SpecError(f"{title}: missing headers")
        while rows and not any(value != "" for value in rows[-1]):
            rows.pop()
        width = len(headers)
        if any(any(value != "" for value in row[width:]) for row in rows) or rows[0][:width] != headers:
            raise SpecError(f"{title}: unexpected headers or extra columns")
        normalized[title] = [list(row[:width]) + [""] * max(0, width - len(row)) for row in rows[1:] if any(value != "" for value in row)]
    return normalized


def parse_json(value, expected, context):
    if value == "":
        return None
    if not isinstance(value, str):
        raise SpecError(f"{context}: expected JSON text")
    def pairs(values):
        result = {}
        for key, item in values:
            if key in result:
                raise SpecError(f"{context}: duplicate JSON key {key}")
            result[key] = item
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except ValueError as exc:
        raise SpecError(f"{context}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, expected):
        raise SpecError(f"{context}: wrong JSON value type")
    from slbt.schema import literal
    literal(parsed, context)
    return parsed


def update_optional(target, key, value):
    if value is None or value == "":
        target.pop(key, None)
    else:
        target[key] = value


ABSENT = object()


def reconcile(before, after, authored=ABSENT):
    """Keep unchanged inherited leaves inherited, rather than freezing defaults."""
    if defaults.equal(before, after):
        return copy.deepcopy(authored) if authored is not ABSENT else ABSENT
    if not isinstance(after, dict) or not after:
        return copy.deepcopy(after)
    result = {}
    for key, value in after.items():
        previous = before.get(key, ABSENT) if isinstance(before, dict) else ABSENT
        own = authored.get(key, ABSENT) if isinstance(authored, dict) else ABSENT
        item = reconcile(previous, value, own)
        if item is not ABSENT:
            result[key] = item
    return result if result else ABSENT


def row_config(row, baseline=None, authored=None):
    config = parse_json(row[12], dict, "Config")
    meta = parse_json(row[15], dict, "Meta")
    if config is not None and "meta" in config:
        raise SpecError("Config must exclude metadata; edit it through Meta")
    if baseline is None:
        result = dict(config or {})
        if meta is not None:
            result["meta"] = meta
        return result if config is not None or meta is not None else None
    own = copy.deepcopy(authored or {})
    own_meta = own.pop("meta", ABSENT)
    old_config = parse_json(baseline[12], dict, "Config")
    old_meta = parse_json(baseline[15], dict, "Meta")
    result = reconcile(old_config, config, own if authored is not None else ABSENT) if config is not None else ABSENT
    new_meta = reconcile(old_meta, meta, own_meta) if meta is not None else ABSENT
    if result is ABSENT:
        result = {}
        explicit = False
    else:
        explicit = True
    if new_meta is not ABSENT:
        result["meta"] = new_meta
    return result if explicit or new_meta is not ABSENT else None


def distinct_cell(value):
    if value == "":
        return None
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.strip().upper() in {"TRUE", "FALSE"}:
        return value.strip().upper() == "TRUE"
    raise SpecError("Distinct: expected TRUE, FALSE, or a blank cell")


def import_models(rows, models, *, dialect="spark"):
    by_name = {model.name: model for model in models}
    grouped, current = {}, None
    for row in rows:
        kind, name = row[:2]
        if not isinstance(kind, str) or kind not in {"TABLE", "SNAPSHOT", "STEP", "OUTPUT", "COLUMN"}:
            raise SpecError(f"unknown catalog row type: {kind}")
        identifier(name, "catalog Name")
        if kind == "TABLE":
            if name not in by_name or name in grouped:
                raise SpecError(f"unexpected, renamed, or duplicate model: {name}")
            grouped[name] = []
            current = name
        if current is None:
            raise SpecError("catalog must begin with a TABLE row")
        grouped[current].append(row)
    if set(grouped) != set(by_name):
        raise SpecError("every selected model must remain present")
    changes = {}
    for name, values in grouped.items():
        model = by_name[name]
        baseline = model_rows(model)
        expressions = {(row[0], row[1]): row[9] for row in baseline if row[0] in {"STEP", "OUTPUT"}}
        if values == baseline:
            continue
        data = {"version": 1, "name": name}
        table = values[0]
        def applicable(row, indices):
            if any(value != "" for i, value in enumerate(row) if i not in {0, 1, *indices}):
                raise SpecError(f"{row[0]}: nonblank inapplicable cells")
        applicable(table, {2, 3, 4, 11, 12, 13, 14, 15})
        if table[2] != "1":
            raise SpecError("TABLE Version must be 1")
        data["layer"], data["domain"] = table[3], table[4]
        if table[11] or model.authored.get("description") == "":
            data["description"] = table[11]
        config = row_config(table, baseline[0], model.authored.get("config"))
        if config is not None:
            data["config"] = config
        for key, index in (("constraints", 13), ("data_tests", 14)):
            value = parse_json(table[index], list, key)
            if value is not None:
                data[key] = value
        steps, output, transformation = {}, None, None
        snapshot, indicators, expected_keys = None, {}, {}
        for row_index, row in enumerate(values[1:], 1):
            kind, item_name = row[:2]
            if kind == "SNAPSHOT":
                applicable(row, {12, 15})
                if row_index != 1 or snapshot is not None or not row[12]:
                    raise SpecError("SNAPSHOT must immediately follow TABLE and have Config")
                original = model.authored.get("snapshot", {})
                old = next((r for r in baseline if r[0] == "SNAPSHOT"), None)
                config = row_config(row, old, original.get("config")) if old else row_config(row)
                snapshot = {"name": item_name, "config": config or {}, "columns": []}
                continue
            if kind in {"STEP", "OUTPUT"}:
                applicable(row, {5, 9, 11})
                if output is not None:
                    raise SpecError("OUTPUT must be the final transformation")
                expression = text(row[9], "Expression")
                original = model.output if kind == "OUTPUT" else model.steps.get(item_name, {})
                if expression == expressions.get((kind, item_name)):
                    transformation = {key: copy.deepcopy(value) for key, value in original.items() if key not in {"columns", "description", "distinct"}}
                else:
                    transformation = parse_expression(expression, dialect=dialect) if re.match(r"^\s*FROM\b", expression, re.I) else {"sql": expression}
                distinct = distinct_cell(row[5])
                if distinct is not None:
                    if "sql" in transformation:
                        raise SpecError("Distinct belongs only on structured STEP/OUTPUT rows, not raw SQL")
                    transformation["distinct"] = distinct
                if row[11] != "" or original.get("description") == "":
                    transformation["description"] = text(row[11], "Description", empty=True)
                transformation["columns"] = {}
                if kind == "STEP":
                    if item_name in steps:
                        raise SpecError("duplicate STEP")
                    steps[item_name] = transformation
                else:
                    if item_name != name:
                        raise SpecError("OUTPUT Name must match TABLE Name")
                    output = transformation
            elif kind == "COLUMN":
                applicable(row, {6, 7, 8, 9, 10, 11, 12, 13, 14, 15})
                if transformation is None:
                    raise SpecError("COLUMN must follow STEP or OUTPUT")
                if item_name in transformation["columns"]:
                    raise SpecError("duplicate COLUMN")
                column = {}
                for key, index in (("expression", 9), ("data_type", 10), ("description", 11)):
                    if row[index] != "" or key != "expression" and original.get("columns", {}).get(item_name, {}).get(key) == "":
                        column[key] = text(row[index], key, empty=key != "expression")
                previous = original.get("columns", {}).get(item_name, {}).get("expression")
                if previous is not None and row[9] == compact_expression(previous, dialect):
                    column["expression"] = previous
                config = row_config(row)
                if config is not None:
                    column["config"] = config
                for key, index in (("constraints", 13), ("data_tests", 14)):
                    value = parse_json(row[index], list, key)
                    if value is not None:
                        column[key] = value
                transformation["columns"][item_name] = column
                if output is not None:
                    expected_keys[item_name] = (row[6], row[7])
                    if snapshot is not None:
                        if row[8] == "":
                            raise SpecError("every output column requires a Snapshot indicator")
                        indicators[item_name] = distinct_cell(row[8])
                    elif row[8]:
                        raise SpecError("Snapshot indicators require a SNAPSHOT row")
                elif any(row[i] for i in (6, 7, 8)):
                    raise SpecError("key/reference/snapshot indicators belong only on output columns")
            else:
                raise SpecError("unexpected TABLE inside a model")
        if output is None:
            raise SpecError("every model requires OUTPUT")
        for value in [*steps.values(), output]:
            value["columns"] = compact_columns(value["columns"])
        data.pop("steps", None)
        data.pop("output", None)
        if steps:
            data["steps"] = steps
        data["output"] = output
        from slbt.catalog.keys import summaries
        if summaries(data) != expected_keys:
            raise SpecError("Key/References must match native constraints/tests/meta")
        if snapshot is not None:
            previous = model.snapshot["columns"] if model.snapshot else []
            snapshot["columns"] = [n for n in previous if indicators.get(n)] + [n for n in output["columns"] if indicators.get(n) and n not in previous]
            data["snapshot"] = snapshot
        changes[name] = data
    return changes


def import_macros(project, catalog, rows):
    existing = {(macro_folder(definition.path, project.path.parent), definition.kind, definition.name): definition
                for definition in project.macros.definitions.values() if definition.path in catalog.macro_files}
    seen, edits, descriptions = set(), {}, {}
    current_folder = None
    for row in rows:
        relative, name, expression, description = row
        if relative and not any((name, expression, description)):
            current_folder = relative
            if not relative.endswith("/"):
                raise SpecError("macro folder rows must end in /")
            continue
        relative = relative or current_folder
        text(relative, "macro Path")
        current_folder = relative
        identifier(name, "macro Name")
        text(expression, "macro Expression")
        text(description, "macro Description", empty=True)
        if relative not in {".", "./"} and (
            Path(relative).is_absolute() or any(c in relative for c in "\\:*?[]")
            or any(part in {"", "."} for part in relative.rstrip("/").split("/"))
            or any(ord(c) < 32 for c in relative) or Path(relative.rstrip("/")).suffix.lower() == ".sql"
        ):
            raise SpecError("macro Path must be a project-relative parent folder, not a SQL filename (for example macros/)")
        folder = project.path.parent / relative
        safe_path(folder)
        folder = folder.resolve()
        if not folder.is_relative_to(project.macro_root):
            raise SpecError("macro Path must be a relative parent folder within the configured macro_path")
        relative = macro_folder(folder / (name + ".sql"), project.path.parent)
        blocks = list(BLOCK.finditer(expression))
        if len(blocks) != 1 or blocks[0].group().strip() != expression.strip() or blocks[0].group(2) != name:
            raise SpecError("macro Expression must contain exactly one complete matching definition")
        definition_key = ("test_" if blocks[0].group(1) == "test" else "") + name
        key = (relative, blocks[0].group(1), name)
        if key in seen:
            raise SpecError("duplicate macro row")
        seen.add(key)
        original_definition = project.macros.definitions.get(definition_key)
        if key not in existing and original_definition is not None:
            raise SpecError("existing definitions cannot be renamed or moved")
        path = existing[key].path if key in existing else folder / (("test_" if blocks[0].group(1) == "test" else "") + name + ".sql")
        safe_path(path)
        if not selected(path, project.macro_root, catalog.macro_selections):
            raise SpecError("cannot edit an unselected macro path")
        if key in existing:
            if original_definition is None or original_definition.path != path:
                raise SpecError("existing definitions cannot change kind, name, or path")
            before = existing[key]
            if expression != before.expression:
                content = edits.get(path, path.read_text(encoding="utf-8"))
                edits[path] = content.replace(before.expression, expression, 1)
            if description != project.macros.descriptions.get(definition_key, ""):
                descriptions[definition_key] = description
        else:
            if path.exists() or path in edits:
                raise SpecError("new macro declarations require an unoccupied path")
            content = ""
            edits[path] = content.rstrip() + "\n\n" + expression.strip() + "\n"
            if description:
                descriptions[definition_key] = description
    if existing.keys() - seen:
        raise SpecError("every exported macro/test must remain present; renaming, moving, and deletion are not supported")
    properties = copy.deepcopy(project.macros.property_files)
    changed_properties = set()
    for name, description in descriptions.items():
        location = next(((path, item) for path, value in properties.items() for item in value.get("macros", []) if item["name"] == name), None)
        if location:
            path, item = location
            if path not in catalog.macro_files:
                raise SpecError("cannot edit descriptions in an unselected macro properties file")
        else:
            path = project.macro_root / "schema.yml"
            if not selected(path, project.macro_root, catalog.macro_selections):
                raise SpecError("macro description requires an allowed schema.yml path")
            item = {"name": name}
            properties.setdefault(path, {"version": 2, "macros": []}).setdefault("macros", []).append(item)
        update_optional(item, "description", description)
        changed_properties.add(path)
    return {**{path: content.encode("utf-8") for path, content in edits.items()}, **{path: dump_yaml(properties[path]) for path in changed_properties}}


def apply_changes(changes):
    """Stage every proposed file first, replace files, roll back on write failure."""
    staged, originals = {}, {}
    try:
        for path, content in changes.items():
            safe_path(path)
            originals[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".slbt-", delete=False) as file:
                staged[path] = Path(file.name)
                file.write(content)
        written = []
        try:
            for path, temporary in staged.items():
                os.replace(temporary, path)
                written.append(path)
        except OSError:
            for path in reversed(written):
                if originals[path] is None:
                    path.unlink()
                else:
                    path.write_bytes(originals[path])
            raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def import_catalog(project, path: Path, *, name=None, dry_run=False):
    catalogs = choose(project, [name] if name is not None else None)
    if len(catalogs) != 1:
        raise SpecError("import requires --catalog when multiple catalogs are configured")
    catalog = catalogs[0]
    excel = path.suffix.lower() == ".xlsx"
    expected = catalog_rows(project, catalog, excel=excel)
    rows = read_catalog(path, expected)
    changes, model_edits, source_edits, group_edits = {}, {}, {}, {}
    for title, models in catalog.sheets.items():
        model_edits.update(import_models(rows[title], models, dialect=project.dialect))
    for title, tables in catalog.source_sheets.items():
        tables_changed, groups_changed = import_sources(rows[title], tables, project)
        source_edits.update(tables_changed)
        for group, settings in groups_changed.items():
            if group in group_edits and not defaults.equal(group_edits[group], settings):
                raise SpecError("repeated SOURCE rows must agree")
            group_edits[group] = settings
    if excel:
        for title in catalog.macro_sheets:
            for target, content in import_macros(project, catalog, rows[title]).items():
                if target in changes and changes[target] != content:
                    raise SpecError(f"conflicting edits across macro sheets: {target}")
                changes[target] = content
    if excel and catalog.defaults_sheet:
        candidate = {"name": project.default_configs.get("name", "Defaults")}
        for group in ("configs", "snapshot_configs"):
            if group in project.default_configs:
                candidate[group] = []
        seen = set()
        for rule_name, config, selectors, group in rows[catalog.defaults_sheet]:
            text(rule_name, "defaults rule name")
            if group not in {"configs", "snapshot_configs"}:
                raise SpecError("defaults Type must be configs or snapshot_configs")
            if (group, rule_name) in seen:
                raise SpecError("duplicate defaults rule")
            seen.add((group, rule_name))
            config = parse_json(config, dict, "defaults config")
            selectors = parse_json(selectors, (dict, list), "defaults target")
            if config is None or selectors is None:
                raise SpecError("defaults require Config and Target")
            candidate.setdefault(group, []).append({"name": rule_name, "config": config, "target": selectors})
        defaults.validate(candidate)
        if not defaults.equal(candidate, {"name": project.default_configs.get("name", "Defaults"), **project.default_configs}):
            data = copy.deepcopy(project.authored)
            data["dbt"]["default_configs"] = candidate
            changes[project.path] = dump_yaml(data)
    if excel:
        registry, layer = {"version": 1, "layers": []}, None
        for layer_name, domain_name, description in rows["classifications"]:
            if layer_name and domain_name:
                raise SpecError("classification rows cannot contain both layer and domain")
            if layer_name:
                layer = {"name": layer_name, "description": description, "domains": []}
                registry["layers"].append(layer)
            elif domain_name and layer is not None:
                layer["domains"].append({"name": domain_name, "description": description})
            else:
                raise SpecError("domain rows require a preceding layer")
        classifications.validate(registry)
        if registry != project.classifications:
            changes[project.registry_path] = dump_yaml(registry)
    for doc in project.documents:
        if any(m["name"] in model_edits for m in doc.models) or any(g["name"] in group_edits or any(g["name"] + "." + t["name"] in source_edits for t in g["tables"]) for g in doc.sources):
            changes[doc.path] = document.render(doc, model_edits, source_edits, group_edits)
    changes = {path: content for path, content in changes.items() if not path.exists() or path.read_bytes() != content}
    from slbt.project import load_project

    load_project(project.path, changes)
    if not dry_run:
        apply_changes(changes)
    return sorted(changes)


def import_sources(rows, tables, project):
    rebuilt, groups, seen, current_group, current_table = {}, {}, set(), None, None
    for row in rows:
        kind, name = row[:2]
        allowed = {"SOURCE": {4, 5, 7, 10, 11, 13, 14}, "TABLE": {2, 3, 6, 8, 10, 11, 12, 13, 14}, "COLUMN": {8, 9, 10, 11, 12, 13, 14}}
        if kind not in allowed or any(v != "" for i, v in enumerate(row) if i not in {0, 1, *allowed.get(kind, set())}):
            raise SpecError("invalid source row type or inapplicable cell")
        text(name, "source Name")
        value = {"name": name}
        extras = parse_json(row[14], dict, "Properties") or {}
        supported = {"quoting"} if kind == "SOURCE" else {"constraints", "docs", "quoting", "external"} if kind == "TABLE" else {"constraints", "docs", "quote"}
        if extras.keys() - supported:
            raise SpecError("Properties duplicates dedicated fields or contains unsupported properties")
        value.update(extras)
        for key, index in (("layer", 2), ("domain", 3), ("database", 4), ("schema", 5), ("identifier", 6), ("loader", 7), ("data_type", 8), ("source_data_type", 9), ("description", 10)):
            if row[index]:
                value[key] = row[index]
        config = parse_json(row[11], dict, "Config")
        meta = parse_json(row[13], dict, "Meta")
        if config is not None and "meta" in config:
            raise SpecError("Config must exclude meta")
        if config is not None or meta is not None:
            value["config"] = config or {}
            if meta is not None:
                value["config"]["meta"] = meta
        tests = parse_json(row[12], list, "Tests")
        if tests is not None:
            value["data_tests"] = tests
        if kind == "SOURCE":
            if name not in project.sources:
                raise SpecError("unexpected or renamed SOURCE")
            current_group, current_table = name, None
            source_properties(value, GROUP, "source group")
            settings = {k: v for k, v in value.items() if k != "name"}
            if name in groups and not defaults.equal(groups[name], settings):
                raise SpecError("repeated SOURCE rows must agree")
            groups[name] = settings
        elif kind == "TABLE":
            if current_group is None or name not in tables or name not in project.sources[current_group]["tables"] or name in seen:
                raise SpecError("unexpected, duplicate, or renamed source TABLE")
            seen.add(name)
            value["name"] = tables[name]["name"]
            value["columns"] = {}
            rebuilt[name], current_table = value, value
        else:
            if current_table is None:
                raise SpecError("source COLUMN must follow TABLE")
            if name in current_table["columns"]:
                raise SpecError("duplicate source COLUMN")
            current_table["columns"][name] = {k: v for k, v in value.items() if k != "name"}
    if seen != tables.keys():
        raise SpecError("every selected source TABLE must remain exactly once")
    rebuilt = {name: value for name, value in rebuilt.items() if not defaults.equal(value, tables[name])}
    groups = {name: value for name, value in groups.items() if not defaults.equal(value, {k: v for k, v in project.sources[name].items() if k not in {"name", "tables"}})}
    return rebuilt, groups
