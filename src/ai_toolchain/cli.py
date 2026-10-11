"""CLI composition root; previews are read-only by default."""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
import uuid

from platformdirs import user_state_path
import typer

from .config import ConfigError, load_config, resolve_config_dir
from .config_setup import ConfigInitializer
from .storage.adapters.local_fs import LocalFileSystem
from .storage.adapters.process_guard import PsutilProcessGuard
from .storage.adapters.rich_progress import NullProgressSink, RichProgressSink
from .storage.adapters.sha256 import Sha256Hasher
from .storage.adapters.warehouse_journal import WarehouseJournal
from .storage.consolidation import ConsolidationService
from .storage.migration import MigrationService
from .storage.models import StorageError
from .storage.planner import StoragePlanner
from .storage.recovery import RecoveryService
from .targets import select_targets, unmapped_directories


app = typer.Typer(help="Share one model warehouse across A1111 and ComfyUI.", no_args_is_help=True)


class LinkMode(str, Enum):
    replace = "replace"
    nested = "nested"


class Application(str, Enum):
    all = "all"
    a1111 = "a1111"
    comfyui = "comfyui"


def _new_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]


@app.callback()
def main(
    ctx: typer.Context,
    config_dir: Optional[Path] = typer.Option(None, "--config-dir", help="Private directory containing paths.yaml and mapping.yaml"),
    state_dir: Optional[Path] = typer.Option(None, "--state-dir"),
    no_progress: bool = typer.Option(False, "--no-progress"),
) -> None:
    ctx.obj = (config_dir, state_dir, no_progress)


@app.command("init-config")
def init_config(ctx: typer.Context) -> None:
    """Create editable local settings from templates; existing settings are preserved."""
    try:
        directory = resolve_config_dir(ctx.obj[0])
        initializer = ConfigInitializer(LocalFileSystem(), Path(__file__).parent / "default_config")
        for path in initializer.initialize(directory):
            typer.echo(f"Created {path}")
        typer.echo("Fill in paths.yaml and review mapping.yaml, then run a preview command.")
    except (ConfigError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _services(ctx: typer.Context):
    config_dir, state_dir, no_progress = ctx.obj
    config = load_config(config_dir)
    fs = LocalFileSystem()
    progress = NullProgressSink() if no_progress else RichProgressSink()
    hasher = Sha256Hasher(progress)
    root = state_dir or config.warehouse / ".ai_toolchain"
    journal = WarehouseJournal(fs, root, _new_id, user_state_path("ai-toolchain") / "migrations")
    return config, fs, progress, hasher, journal, PsutilProcessGuard()


def _plan(ctx: typer.Context, application: str, version: str | None, mode: LinkMode | None):
    config, fs, progress, hasher, journal, guard = _services(ctx)
    targets = select_targets(config, application, version)
    plan = StoragePlanner(fs, hasher, progress).build(targets, mode.value if mode else config.link_mode, config.warehouse)
    return config, fs, progress, hasher, journal, guard, plan


def _render(plan, config) -> None:
    typer.echo(f"Mode: {plan.mode}")
    for entry in plan.entries:
        suffix = f" ({entry.detail})" if entry.detail else ""
        if entry.files:
            suffix += f" [{len(entry.files)} files; {sum(f.size for f in entry.files):,} bytes; {sum(f.action == 'duplicate' for f in entry.files)} identical collisions]"
        typer.echo(f"{entry.action:16} {entry.target.label} -> {entry.target.warehouse}{suffix}")
    for item in unmapped_directories(config, [entry.target for entry in plan.entries]):
        typer.echo(f"unmapped         {item}")
    actions = [item.action for item in plan.entries]
    pending = sum(x in {"migrate", "create_nested", "repair_nested", "switch_nested"} for x in actions)
    missing = sum(x in {"missing_source", "missing_target"} for x in actions)
    typer.echo(f"Status: {actions.count('noop')} linked, {pending} pending, {missing} missing, {actions.count('conflict')} conflicts")


def _run(ctx: typer.Context, application: str, version: str | None, mode: LinkMode | None, apply: bool) -> None:
    try:
        config, fs, progress, hasher, journal, guard, plan = _plan(ctx, application, version, mode)
        _render(plan, config)
        if plan.blocked:
            raise typer.Exit(2)
        if apply:
            recovery = RecoveryService(fs, hasher, journal, progress, guard)
            migration_id = MigrationService(fs, hasher, journal, progress, guard, recovery).apply(plan)
            typer.echo(f"Applied.{f' Migration ID: {migration_id}' if migration_id else ''}")
        else:
            typer.echo("Preview only. Pass --apply to make changes.")
    except (ConfigError, StorageError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def a1111(ctx: typer.Context, mode: Optional[LinkMode] = typer.Option(None, "--mode"), apply: bool = typer.Option(False, "--apply")) -> None:
    _run(ctx, "a1111", None, mode, apply)


@app.command()
def comfyui(ctx: typer.Context, version: Optional[str] = typer.Option(None, "--version"), mode: Optional[LinkMode] = typer.Option(None, "--mode"), apply: bool = typer.Option(False, "--apply")) -> None:
    _run(ctx, "comfyui", version, mode, apply)


@app.command()
def check(ctx: typer.Context, application: Application = typer.Argument(Application.all), version: Optional[str] = typer.Option(None, "--version"), mode: Optional[LinkMode] = typer.Option(None, "--mode")) -> None:
    try:
        config, _, _, _, _, _, plan = _plan(ctx, application.value, version, mode)
        _render(plan, config)
        if any(e.action != "noop" for e in plan.entries):
            raise typer.Exit(1)
    except (ConfigError, StorageError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def migrations(ctx: typer.Context) -> None:
    try:
        _, _, _, _, journal, _ = _services(ctx)
        new = journal.list_records()
        records = new + [r for r in journal.legacy_records() if r.get("id") not in {n["id"] for n in new}]
        if not records:
            typer.echo("No migrations recorded.")
        for record in records:
            typer.echo(f"{record['id']}  {record.get('status', 'unknown')}  {len(record.get('entries', []))} folders")
    except (ConfigError, StorageError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def consolidate(ctx: typer.Context, migration: Optional[str] = typer.Option(None, "--migration"), apply: bool = typer.Option(False, "--apply")) -> None:
    try:
        config, fs, progress, hasher, journal, guard = _services(ctx)
        ids = [migration] if migration else [r["id"] for r in journal.legacy_records() if r.get("status") == "applied" and r.get("entries") and all(e.get("label", "").startswith("comfyui/") for e in r["entries"])]
        service = ConsolidationService(fs, hasher, journal, progress, guard, config.warehouse, list(config.comfyui_versions.values()))
        manifests = service.apply_many(ids) if apply and ids else [service.preview(value) for value in ids]
        for record in manifests:
            redundant = sum(f["size"] for e in record["entries"] for f in e["files"] if f["action"] == "duplicate")
            typer.echo(f"{record['id']}: {len(record['entries'])} folders; {redundant:,} redundant bytes; {record['status']}")
        if not ids:
            typer.echo("No eligible legacy ComfyUI migrations.")
        elif apply:
            typer.echo("Consolidation complete; active application links retained.")
        else:
            typer.echo("Preview only. Pass --apply to consolidate.")
    except (ConfigError, StorageError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def resume(ctx: typer.Context, migration_id: str = typer.Argument(...), apply: bool = typer.Option(False, "--apply")) -> None:
    """Resume a journaled migration, consolidation, or rollback."""
    try:
        config, fs, progress, hasher, journal, guard = _services(ctx)
        record = journal.load(migration_id)
        typer.echo(f"Migration {migration_id}: {record['origin']} / {record['status']}")
        if not apply:
            typer.echo("Preview only. Pass --apply to resume.")
            return
        if record["status"] == "rolling_back":
            RecoveryService(fs, hasher, journal, progress, guard).rollback(record)
        elif record["origin"] == "legacy":
            service = ConsolidationService(fs, hasher, journal, progress, guard, config.warehouse, list(config.comfyui_versions.values()))
            service.apply_many([migration_id])
        elif record["origin"] == "new":
            recovery = RecoveryService(fs, hasher, journal, progress, guard)
            MigrationService(fs, hasher, journal, progress, guard, recovery).resume(record)
        else:
            raise StorageError(f"Unknown migration origin: {record['origin']}")
        typer.echo("Resume complete.")
    except (ConfigError, StorageError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def rollback(ctx: typer.Context, migration_id: str = typer.Argument(...), apply: bool = typer.Option(False, "--apply")) -> None:
    try:
        _, fs, progress, hasher, journal, guard = _services(ctx)
        record = journal.load(migration_id)
        typer.echo(f"Migration {migration_id}: {record['status']} ({len(record['entries'])} folders)")
        for entry in record["entries"]:
            typer.echo(f"restore {entry['source']} from verified snapshots")
        if apply:
            RecoveryService(fs, hasher, journal, progress, guard).rollback(record)
            typer.echo("Rollback complete; warehouse models retained.")
        else:
            typer.echo("Preview only. Pass --apply to restore application folders.")
    except (ConfigError, StorageError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


if __name__ == "__main__":
    app()
