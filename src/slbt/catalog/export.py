"""Pure XLSX/CSV review and resolved semantic JSON rendering."""

import copy
import csv
import io
import json

from sqlglot import exp

from slbt.catalog.lineage import ColumnLineage
from slbt.schema import SpecError, dotted
from slbt.sql import Compiler, sql


def semantic_catalog(project, catalog):
    from slbt.project import transformation_tree
    selected_models = {m.name for models in catalog.sheets.values() for m in models}
    selected_sources = {name for tables in catalog.source_sheets.values() for name in tables}
    macro_refs = set()
    models = []
    lineage = ColumnLineage(project)
    for model in project.models:
        if model.name not in selected_models:
            continue
        compiler = Compiler(project.macros)
        compiler.logical_runtime = True
        transformations, references = {}, set()
        for name, value in [*model.steps.items(), ("slbt_output", model.output)]:
            tree = transformation_tree(value, compiler, model.name)
            from slbt.sql import read_query, scalar_tree
            authored = read_query(value["sql"], model.name, project.dialect) if "sql" in value else tree
            for call in authored.find_all(exp.Anonymous):
                if call.name.lower() in project.macros.definitions:
                    references.add(call.name.lower())
            item = {"sql": sql(tree, project.dialect), "columns": lineage.render(tree, value["columns"], model)}
            if "description" in value:
                item["description"] = value["description"]
            transformations[name] = item
        # Runtime refs can be hidden inside statically expanded wrappers.
        references.update(compiler.deferred_refs if hasattr(compiler, "deferred_refs") else ())
        pending = list(references)
        while pending:
            name = pending.pop()
            definition = project.macros.definitions[name]
            for called in definition.calls:
                if called in project.macros.definitions and called not in references:
                    references.add(called)
                    pending.append(called)
        runtime = sorted(name for name in references if project.macros.definitions[name].runtime)
        macro_refs.update(references)
        data = {k: copy.deepcopy(v) for k, v in model.authored.items() if k not in {"steps", "output", "config", "snapshot"}}
        data["config"] = copy.deepcopy(model.config)
        data["steps"] = [{"name": name, **value} for name, value in transformations.items() if name != "slbt_output"]
        data["output"] = transformations["slbt_output"]
        if model.snapshot:
            data["snapshot"] = copy.deepcopy(model.snapshot)
        data["dependencies"] = [{"relation": dotted(dep), "included": dep.get("ref") in selected_models if "ref" in dep else ".".join(dep["source"]) in selected_sources} for dep in model.dependencies]
        data["expansion"] = {"status": "deferred" if runtime else "expanded", "macro_refs": sorted(references)}
        data["macro_refs"] = sorted(references)
        models.append(data)
    macro_refs.update(name for name, definition in project.macros.definitions.items() if definition.path in catalog.macro_files)
    pending = list(macro_refs)
    while pending:
        for name in project.macros.definitions[pending.pop()].calls:
            if name in project.macros.definitions and name not in macro_refs:
                macro_refs.add(name)
                pending.append(name)
    macros = [{"name": name, "kind": definition.kind, "path": definition.path.relative_to(project.macro_root).as_posix(), "expression": definition.expression, "description": project.macros.descriptions.get(name, ""), "runtime": definition.runtime, "macro_refs": sorted(definition.calls & macro_refs)} for name, definition in sorted(project.macros.definitions.items()) if name in macro_refs]
    sources = []
    for group in project.sources.values():
        tables = []
        for identity, table in group["tables"].items():
            if identity in selected_sources:
                tables.append({**{k: copy.deepcopy(v) for k, v in table.items() if k != "columns"}, "identity": identity, "columns": [{"name": n, **copy.deepcopy(c)} for n, c in table["columns"].items()]})
        if tables:
            sources.append({**{k: copy.deepcopy(v) for k, v in group.items() if k != "tables"}, "tables": tables})
    return {"format": "slbt.catalog", "format_version": 1, "representation": "resolved", "catalog": catalog.name, "dialect": project.dialect, "models": models, "sources": sources, "macros": macros, "classifications": copy.deepcopy(project.classifications), "default_configs": copy.deepcopy(project.default_configs)}


def catalog_outputs(configuration, catalog, format=None):
    from slbt.catalog.review import choose, catalog_rows, workbook_bytes, safe_name
    if isinstance(catalog, str):
        catalog = choose(configuration, [catalog])[0]
    format = format or catalog.format
    if format == "json":
        return {catalog.name + ".json": (json.dumps(semantic_catalog(configuration, catalog), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()}
    if format not in {"xlsx", "csv"}:
        raise SpecError("catalog format must be xlsx, csv, or json")
    sheets = catalog_rows(configuration, catalog, excel=format == "xlsx")
    if format == "xlsx":
        return {catalog.name + ".xlsx": workbook_bytes(sheets)}
    result = {}
    for title, (headers, rows) in sheets.items():
        safe_name(title, "CSV filename")
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows([headers, *rows])
        result["csv/" + title + ".csv"] = stream.getvalue().encode()
    return result
