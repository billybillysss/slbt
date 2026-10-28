"""Generated-file comparison, preflight, writes, and explicit optional pruning."""

from pathlib import Path, PurePosixPath

from slbt.paths import safe_path, subpath as parse_subpath
from slbt.schema import SpecError


def reconcile(destination, kind, outputs, dry_run=False, *, subpath=None, prune=False, formats=()):
    destination = Path(destination).absolute()
    safe_path(destination)
    if destination.exists() and not destination.is_dir():
        raise SpecError("output destination must be a directory")
    if kind not in {"dbt", "catalog"}:
        raise SpecError("reconciliation kind must be dbt or catalog")
    formats = set(formats)
    if formats - {"xlsx", "csv", "json"}:
        raise SpecError("invalid catalog reconciliation formats")
    scope = parse_subpath(str(subpath)) if subpath and str(subpath) != "." else Path()
    checked = {}
    for name, content in outputs.items():
        name = str(name)
        path = PurePosixPath(name)
        if not name or path == PurePosixPath(".") or path.is_absolute() or "\\" in name or ":" in name or ".." in path.parts:
            raise SpecError(f"unsafe generated relative path: {name}")
        if kind == "dbt":
            valid = len(path.parts) > 1 and path.parts[0] in {"models", "macros", "snapshots"} and path.suffix.lower() in {".sql", ".yml", ".yaml"}
            valid = valid and Path(*path.parts[1:]).is_relative_to(scope)
        else:
            valid = (len(path.parts) == 1 and path.suffix in {".xlsx", ".json"}) or (len(path.parts) == 2 and path.parts[0] == "csv" and path.suffix == ".csv")
        if not valid or not isinstance(content, bytes):
            raise SpecError(f"invalid {kind} output: {name}")
        if name.casefold() in {n.casefold() for n in checked}:
            raise SpecError(f"case-insensitive output collision: {name}")
        target = destination.joinpath(*path.parts)
        safe_path(target)
        if target.exists() and not target.is_file():
            raise SpecError(f"output file collides with a directory: {target}")
        for parent in target.parents:
            if parent.exists() and not parent.is_dir():
                raise SpecError(f"output parent must be a directory: {parent}")
        checked[name] = (target, content)
    candidates = []
    if prune:
        if kind == "dbt":
            for category in ("models", "macros", "snapshots"):
                root = destination / category / scope
                safe_path(root, tree=True)
                if root.exists() and not root.is_dir():
                    raise SpecError(f"managed output must be a directory: {root}")
                if root.exists():
                    candidates.extend(p for p in root.rglob("*") if p.is_file())
        else:
            for format in formats:
                root = destination / "csv" if format == "csv" else destination
                safe_path(root, tree=format == "csv")
                if root.exists():
                    candidates.extend(root.rglob("*.csv") if format == "csv" else root.glob("*." + format))
    wanted = {target for target, _ in checked.values()}
    actions = [("update" if target.exists() else "create", target) for target, content in checked.values() if not target.exists() or target.read_bytes() != content]
    actions.extend(("remove", target) for target in candidates if target not in wanted)
    for _, target in actions:
        safe_path(target)
        if target.exists() and not target.is_file():
            raise SpecError(f"existing target must be a file: {target}")
    actions.sort(key=lambda item: item[1].as_posix())
    if not dry_run:
        contents = {target: content for target, content in checked.values()}
        for action, target in actions:
            if action == "remove":
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents[target])
    return actions


def export_dbt(project, *, path=None, subdirectory=None, dry_run=False, prune=False):
    from slbt.compiler.dbt import dbt_outputs
    scope = parse_subpath(subdirectory)
    if subdirectory and not any((root / scope).is_dir() for root in (project.model_root, project.macro_root)):
        raise SpecError("subpath must select an existing input folder")
    return reconcile(project.destination("dbt", path), "dbt", dbt_outputs(project, PurePosixPath(scope.as_posix())), dry_run, subpath=scope, prune=prune)
