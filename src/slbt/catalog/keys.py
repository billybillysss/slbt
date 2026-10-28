"""Derived native key/reference summaries, never independent authoring fields."""

import re


def reference(value):
    value = str(value)
    value = re.sub(r"\{\{\s*ref\(['\"]([^'\"]+)['\"]\)\s*\}\}", r"ref.\1", value)
    return re.sub(r"\{\{\s*source\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\)\s*\}\}", r"source.\1.\2", value)


def summaries(data):
    columns = data["output"]["columns"]
    roles = {name: set() for name in columns}
    refs = {name: [] for name in columns}
    for name, raw in columns.items():
        column = raw if isinstance(raw, dict) else {}
        if column.get("config", {}).get("meta", {}).get("business_key") is True:
            roles[name].add("B")
        for test in column.get("data_tests", []):
            if isinstance(test, dict) and "relationships" in test:
                options = test["relationships"] or {}
                args = options.get("arguments", options)
                roles[name].add("F")
                refs[name].append(reference(args.get("to", "")) + ("." + str(args["field"]) if "field" in args else ""))
    rules = [(rule, rule.get("columns", [])) for rule in data.get("constraints", [])]
    rules += [(rule, [name]) for name, raw in columns.items() if isinstance(raw, dict) for rule in raw.get("constraints", [])]
    for rule, names in rules:
        role = {"primary_key": "P", "foreign_key": "F"}.get(rule.get("type"))
        if role:
            for name in names:
                if name in roles:
                    roles[name].add(role)
                    if role == "F":
                        target = reference(rule.get("to", rule.get("expression", "")))
                        if rule.get("to_columns"):
                            target += "(" + ", ".join(rule["to_columns"]) + ")"
                        if len(names) > 1:
                            target = "(" + ", ".join(names) + ") -> " + target
                        refs[name].append(target)
    return {name: ("".join(role for role in "PFB" if role in roles[name]), "\n".join(dict.fromkeys(refs[name]))) for name in columns}
