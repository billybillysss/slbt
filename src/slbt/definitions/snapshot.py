"""Model-owned snapshot validation."""

from slbt.schema import SpecError, fields, identifier, literal, mapping


def validate(value, columns):
    mapping(value, "snapshot")
    fields(value, {"name", "config", "columns"}, {"name", "config", "columns"}, "snapshot")
    identifier(value["name"], "snapshot.name")
    config = literal(mapping(value["config"], "snapshot.config"))
    selected = value["columns"]
    if not isinstance(selected, list) or not selected or any(not isinstance(n, str) for n in selected) or len(set(selected)) != len(selected) or set(selected) - columns.keys():
        raise SpecError("snapshot columns must be a distinct nonempty output subset")
    def members(names, context):
        names = names if isinstance(names, list) else [names]
        if not names or any(not isinstance(n, str) or n not in selected for n in names) or len(set(names)) != len(names):
            raise SpecError(f"snapshot {context} must contain selected columns")
    members(config.get("unique_key"), "unique_key")
    if config.get("strategy") == "check":
        if config.get("check_cols") != "all":
            if not isinstance(config.get("check_cols"), list):
                raise SpecError("snapshot check_cols must be all or a list")
            members(config["check_cols"], "check_cols")
    elif config.get("strategy") == "timestamp":
        if not isinstance(config.get("updated_at"), str):
            raise SpecError("snapshot updated_at must be a selected column")
        members(config["updated_at"], "updated_at")
    else:
        raise SpecError("snapshot strategy must be check or timestamp")
    if "invalidate_hard_deletes" in config and type(config["invalidate_hard_deletes"]) is not bool:
        raise SpecError("snapshot invalidate_hard_deletes must be boolean")
    return value
