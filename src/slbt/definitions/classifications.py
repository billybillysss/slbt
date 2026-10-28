"""Layer/domain registry validation and Bronze/Silver synchronization."""

import copy

from slbt.schema import SpecError, classification, fields, mapping, version


def validate(data):
    mapping(data, "classifications")
    fields(data, {"version", "layers"}, {"version", "layers"}, "classifications")
    version(data["version"], "classifications")
    if not isinstance(data["layers"], list) or not data["layers"]:
        raise SpecError("classifications.layers must be a nonempty list")
    seen = set()
    for layer in data["layers"]:
        mapping(layer, "layer")
        fields(layer, {"name", "description", "domains"}, {"name", "description", "domains"}, "layer")
        for key in ("name", "description"):
            classification(layer[key], f"layer.{key}")
        if layer["name"].casefold() in seen:
            raise SpecError("duplicate classification layer")
        seen.add(layer["name"].casefold())
        if not isinstance(layer["domains"], list):
            raise SpecError("layer.domains must be a list")
        domains = set()
        for domain in layer["domains"]:
            mapping(domain, "domain")
            fields(domain, {"name", "description"}, {"name", "description"}, "domain")
            for key in ("name", "description"):
                classification(domain[key], f"domain.{key}")
            if domain["name"].casefold() in domains:
                raise SpecError("duplicate classification domain")
            domains.add(domain["name"].casefold())
    return data


def check(data, layer, domain):
    classification(layer, "layer")
    classification(domain, "domain")
    if not any(item["name"] == layer and any(d["name"] == domain for d in item["domains"]) for item in data["layers"]):
        raise SpecError(f"unregistered classification: {layer}/{domain}")


def synchronize(data):
    result = copy.deepcopy(data)
    layers = {item["name"]: item for item in result["layers"]}
    if not {"silver", "bronze"} <= layers.keys():
        raise SpecError("sync-classifications requires silver and bronze layers")
    silver = layers["silver"]["domains"]
    names = {item["name"] for item in silver}
    layers["bronze"]["domains"] = copy.deepcopy(silver) + [d for d in layers["bronze"]["domains"] if d["name"] not in names]
    return validate(result)
