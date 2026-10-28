"""Path boundaries shared by export and import (including Windows junctions)."""

from pathlib import Path, PureWindowsPath

from slbt.schema import SpecError, text


def linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def safe_path(path: Path, *, tree=False) -> None:
    # Do not resolve before checking: resolve would conceal a link or junction.
    for candidate in (path, *path.parents):
        if linked(candidate):
            raise SpecError(f"symlinks and junctions are not allowed: {candidate}")
    if tree and path.exists():
        for candidate in path.rglob("*"):
            if linked(candidate):
                raise SpecError(f"symlinks and junctions are not allowed: {candidate}")


def overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def configured_path(base: Path, value, context: str, *, output=False, override=False) -> Path:
    text(value, context)
    if override:
        # CLI/API overrides may use native separators; YAML paths stay portable.
        value = Path(value).as_posix()
    if "\\" in value:
        raise SpecError(f"{context}: use forward slashes in configuration")
    if not override and (Path(value).is_absolute() or PureWindowsPath(value).drive or ":" in value):
        raise SpecError(f"{context}: expected a project-relative path")
    if not override and not output and ".." in value.split("/"):
        raise SpecError(f"{context}: input traversal is not allowed")
    path = (base / value).absolute()
    safe_path(path)
    return path.resolve()


def subpath(value: str | None) -> Path:
    if value is None:
        return Path()
    text(value, "subpath")
    path = Path(value)
    if path.is_absolute() or ":" in value or "\\" in value or any(p in {".", ".."} for p in value.split("/") if p) or any(c in value for c in "*?[") or path.suffix:
        raise SpecError("subpath must be a relative folder, without traversal, filenames, or globs")
    return path


def validate_destinations(inputs: list[Path], dbt: Path, catalog: Path) -> None:
    if overlaps(dbt, catalog):
        raise SpecError("catalog and dbt destinations must not overlap")
    if any(overlaps(root, output) for root in inputs for output in (dbt, catalog)):
        raise SpecError("input and output paths must not overlap")
