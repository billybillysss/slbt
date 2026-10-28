"""Standalone resolved-model validation (no registry or project defaults)."""

import tempfile
from pathlib import Path

from slbt.macros import Registry


def registry(macros=None, dialect="spark"):
    if isinstance(macros, Registry):
        if macros.dialect != dialect:
            raise ValueError("macro registry dialect differs from compilation dialect")
        return macros
    # An empty root does not need to exist and does not create filesystem inputs.
    result = Registry(Path(tempfile.gettempdir()) / "__slbt_empty_macros__", dialect=dialect)
    if macros:
        raise ValueError("macros must be a slbt.macros.Registry")
    return result


def resolved(model, macros=None, *, dialect="spark"):
    from slbt.project import parse_model

    return parse_model(Path("model.yml"), Path(), None, registry(macros, dialect), data=model)


def validate(model, macros=None, *, dialect="spark"):
    parsed = resolved(model, macros, dialect=dialect)
    return {**parsed.steps, "slbt_output": parsed.output}
