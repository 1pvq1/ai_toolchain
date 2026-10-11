"""Resolve configured application mappings to concrete directories."""

from dataclasses import dataclass
import os
from pathlib import Path

from .config import Config, ConfigError, relative_parts


@dataclass(frozen=True)
class Target:
    app: str
    version: str | None
    root: Path
    source_rel: str
    warehouse_rel: str
    source: Path
    warehouse: Path

    @property
    def label(self) -> str:
        return f"{self.app}{'/' + self.version if self.version else ''}:{self.source_rel}"


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def select_targets(config: Config, app: str = "all", version: str | None = None) -> list[Target]:
    if app not in {"a1111", "comfyui", "all"}:
        raise ConfigError(f"Unknown application: {app}")
    if version and app == "a1111":
        raise ConfigError("--version applies only to ComfyUI")
    if version and version not in config.comfyui_versions:
        raise ConfigError(f"Unknown ComfyUI version: {version}")
    if app == "a1111" and config.a1111_root is None:
        raise ConfigError("A1111 is not configured; set a1111_root in your local paths.yaml")
    if app == "comfyui" and not config.comfyui_versions:
        raise ConfigError("ComfyUI is not configured; add an install to comfyui_versions in your local paths.yaml")
    roots: list[tuple[str, str | None, Path]] = []
    if app in {"a1111", "all"} and config.a1111_root is not None:
        roots.append(("a1111", None, config.a1111_root))
    if app in {"comfyui", "all"}:
        roots.extend(("comfyui", name, root) for name, root in config.comfyui_versions.items() if not version or name == version)

    targets: list[Target] = []
    warehouse_root = config.warehouse.resolve(strict=False)
    for app_name, name, root in roots:
        resolved_root = root.resolve(strict=False)
        for source_rel, warehouse_rel in config.mappings[app_name].items():
            source = root.joinpath(*relative_parts(source_rel, "source"))
            target = config.warehouse.joinpath(*relative_parts(warehouse_rel, "target"))
            # Inspect the source's parent, not the source itself: it may already
            # be a valid link to the warehouse.
            if not _inside(source.parent.resolve(strict=False), resolved_root):
                raise ConfigError(f"Source parent escapes application root: {source}")
            if not _inside(target.resolve(strict=False), warehouse_root):
                raise ConfigError(f"Target escapes warehouse root: {target}")
            targets.append(Target(app_name, name, root, source_rel, warehouse_rel, source, target))
    return targets


def unmapped_directories(config: Config, targets: list[Target]) -> list[str]:
    reports: list[str] = []
    seen_roots: set[tuple[str, str | None]] = set()
    for target in targets:
        key = (target.app, target.version)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        models = target.root / "models"
        mapped = {t.source.name.casefold() for t in targets if (t.app, t.version) == key and t.source.parent == models}
        if models.is_dir():
            for directory in sorted(models.iterdir(), key=lambda p: p.name.casefold()):
                if ".ai-toolchain-backup-" in directory.name:
                    continue
                if directory.is_dir() and directory.name.casefold() not in mapped:
                    reports.append(f"{target.app}{'/' + target.version if target.version else ''}:models/{directory.name}")
    return reports
