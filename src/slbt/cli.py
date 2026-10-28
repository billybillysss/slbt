"""Command orchestration; slbt never executes dbt or warehouse SQL."""

from functools import wraps
from pathlib import Path
from zipfile import BadZipFile

import click

from slbt import __version__
from slbt.catalog import export_catalog, import_catalog
from slbt.definitions.classifications import synchronize
from slbt.enrich import enrich_project
from slbt.export import export_dbt
from slbt.inspection import inspect_plan, inspect_sql
from slbt.optimize import optimize_project
from slbt.paths import safe_path
from slbt.project import Project, load_project, resolve_project
from slbt.scaffold import initialize
from slbt.schema import SpecError, dump_yaml


class Names(click.Option):
    """A repeatable option accepting one or more space-separated names."""
    def __init__(self, *args, **kwargs):
        kwargs["type"] = click.UNPROCESSED
        super().__init__(*args, **kwargs)

    def add_to_parser(self, parser, ctx):
        super().add_to_parser(parser, ctx)
        option = next((parser._long_opt.get(name) or parser._short_opt.get(name) for name in self.opts if parser._long_opt.get(name) or parser._short_opt.get(name)), None)
        original = option.process
        def process(value, state):
            values = [value]
            while state.rargs and not state.rargs[0].startswith("-"):
                values.append(state.rargs.pop(0))
            original(tuple(values), state)
        option.process = process


def guarded(function):
    @wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, OSError, UnicodeError, BadZipFile) as exc:
            raise click.ClickException(str(exc)) from exc
    return run


def names(values):
    return [name for group in values for name in group]


def project(ctx):
    path = resolve_project(ctx.obj)
    if not path.is_file():
        raise click.ClickException("No slbt_project.yaml found; run slbt init or supply --project-dir")
    return Project(path)


def summary(changes, dry_run):
    for change in changes:
        if isinstance(change, tuple):
            action, path = change
            click.echo(f"{action}: {path}")
        else:
            click.echo(f"update: {change}")
    click.echo(f"{'Dry run: ' if dry_run else ''}{len(changes)} file change(s)")


def enrichment_summary(report):
    counts = {key: sum('value' in item and item['property'] == key for item in report) for key in ("data_type", "description")}
    click.echo(f"Filled {counts['data_type']} types, {counts['description']} descriptions; {sum('reason' in item for item in report)} unresolved properties")


@click.group()
@click.version_option(version=__version__, prog_name="slbt")
@click.option("--project-dir", type=click.Path(path_type=Path), envvar="slbt_PROJECT_DIR")
@click.pass_context
def cli(ctx, project_dir):
    """Semantic Layer Build Tool: offline validation, compilation, and review."""
    ctx.obj = project_dir


@cli.command("init")
@click.argument("directory", required=False, default=".", type=click.Path(path_type=Path))
@click.pass_context
@guarded
def init_command(ctx, directory):
    base = resolve_project(ctx.obj).parent
    initialize(directory if directory.is_absolute() else base / directory)
    click.echo(f"Initialized {directory}")


@cli.command()
@click.pass_context
@guarded
def validate(ctx):
    loaded = project(ctx)
    click.echo(f"Validated {len(loaded.models)} models and {len(loaded.source_paths)} source tables offline")


@cli.command("inspect")
@click.argument("table")
@click.option("--columns", cls=Names, multiple=True, help="Output columns; omit for the complete table logic.")
@click.option("--optimize", "optimized", type=click.BOOL, default=True, show_default=True)
@click.option("--view", type=click.Choice(["sql", "plan"]), default="sql", show_default=True)
@click.pass_context
@guarded
def inspect_command(ctx, table, columns, optimized, view):
    """Show expanded table SQL or a logical plan; never write or execute."""
    loaded = project(ctx)
    columns = names(columns) or None
    if view == "plan":
        click.echo("Offline logical plan; not a warehouse execution plan.")
        click.echo(inspect_plan(loaded, table, columns, optimized=optimized))
    else:
        # Comments keep stdout valid SQL when redirected into a file.
        rendered = inspect_sql(loaded, table, columns, optimized=optimized)
        click.echo(f"-- Inspect: {table} | dialect: {loaded.dialect} | optimize: {str(optimized).lower()}")
        click.echo("-- Logical source/ref leaves need physical bindings; not executed. Authored files unchanged.")
        click.echo(rendered)


@cli.group("export")
def export_group():
    """Render and reconcile generated output."""


@export_group.command("dbt")
@click.option("--path")
@click.option("--subpath")
@click.option("--prune", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
@guarded
def dbt_command(ctx, path, subpath, prune, dry_run):
    summary(export_dbt(project(ctx), path=path, subdirectory=subpath, prune=prune, dry_run=dry_run), dry_run)


@export_group.command("catalog")
@click.option("--catalog", cls=Names, multiple=True)
@click.option("--path")
@click.option("--format", type=click.Choice(["xlsx", "csv", "json"]))
@click.option("--prune", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
@guarded
def catalog_command(ctx, catalog, path, format, prune, dry_run):
    summary(export_catalog(project(ctx), names=names(catalog) or None, path=path, format=format, prune=prune, dry_run=dry_run), dry_run)


@cli.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--catalog")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@guarded
def import_command(ctx, path, catalog, dry_run):
    summary(import_catalog(project(ctx), path, name=catalog, dry_run=dry_run), dry_run)


@cli.command("optimize")
@click.option("--select", "-s", cls=Names, multiple=True)
@click.option("--format", type=click.Choice(["pretty", "compact"]))
@click.option("--preserve-macro", "--preserve-marco", type=click.BOOL)
@click.option("--enrich", "enrich_enabled", type=click.BOOL)
@click.option("--dry-run", is_flag=True)
@click.pass_context
@guarded
def optimize_command(ctx, select, format, preserve_macro, enrich_enabled, dry_run):
    result = optimize_project(project(ctx), models=names(select) or None, format=format, preserve_macro=preserve_macro, enrich=enrich_enabled, dry_run=dry_run)
    for name, reason in result.skipped.items():
        click.echo(f"Skipped {name}: {reason}. Model unchanged.", err=True)
    summary(result.changed, dry_run)
    if hasattr(result, "report"):
        enrichment_summary(result.report)


@cli.command("enrich")
@click.option("--select", "-s", cls=Names, multiple=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
@guarded
def enrich_command(ctx, select, dry_run):
    changes, report = enrich_project(project(ctx), names(select), dry_run=dry_run)
    summary(changes, dry_run)
    enrichment_summary(report)


@cli.command("sync-classifications")
@click.option("--dry-run", is_flag=True)
@click.pass_context
@guarded
def sync_command(ctx, dry_run):
    loaded = project(ctx)
    registry = synchronize(loaded.classifications)
    changes = {loaded.registry_path: dump_yaml(registry)} if registry != loaded.classifications else {}
    load_project(loaded.path, changes)
    if not dry_run and changes:
        loaded.registry_path.write_bytes(changes[loaded.registry_path])
    summary(list(changes), dry_run)


def main(argv=None):
    return cli.main(args=argv, prog_name="slbt")
