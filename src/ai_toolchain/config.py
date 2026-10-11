"""Load and validate the two user-editable YAML files."""

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping

from platformdirs import user_config_path
import yaml


class ConfigError(ValueError):
    """The configuration cannot be used safely."""


@dataclass(frozen=True)
class Config:
    warehouse: Path
    a1111_root: Path | None
    comfyui_versions: Mapping[str, Path]
    mappings: Mapping[str, Mapping[str, str]]
    link_mode: str
    config_dir: Path


def _absolute_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a nonempty absolute path")
    if "<" in value or ">" in value:
        raise ConfigError(f"{label} is still a template placeholder; edit your local paths.yaml")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"{label} must be absolute: {value}")
    return path


def relative_parts(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ConfigError(f"{label} must be a forward-slash relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute():
        raise ConfigError(f"{label} must be relative: {value}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ConfigError(f"{label} contains an unsafe path component: {value}")
    return tuple(parts)


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return data


def resolve_config_dir(
    config_dir: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    checkout_root: Path | None = None,
    user_config_dir: Path | None = None,
) -> Path:
    """Resolve private settings; packaged templates are never active defaults."""
    if config_dir is not None:
        return config_dir.expanduser()
    environment = os.environ if environ is None else environ
    override = environment.get("AI_TOOLCHAIN_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    checkout = checkout_root if checkout_root is not None else Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (checkout / "config").is_dir():
        return checkout / "config" / "local"
    return user_config_dir if user_config_dir is not None else user_config_path("ai-toolchain", appauthor=False)


def load_config(config_dir: Path | None = None) -> Config:
    config_dir = resolve_config_dir(config_dir)
    if not all((config_dir / name).is_file() for name in ("paths.yaml", "mapping.yaml")):
        raise ConfigError(
            f"Local configuration is missing at {config_dir}. "
            f'Run ai-toolchain --config-dir "{config_dir}" init-config, '
            "then edit paths.yaml and mapping.yaml."
        )
    paths = _read_yaml(config_dir / "paths.yaml")
    mapping_data = _read_yaml(config_dir / "mapping.yaml")
    if "warehouse_root" in paths and "repo_root" in paths and paths["warehouse_root"] != paths["repo_root"]:
        raise ConfigError("warehouse_root and legacy repo_root disagree; keep only warehouse_root")
    warehouse = _absolute_path(paths.get("warehouse_root", paths.get("repo_root")), "warehouse_root")
    a1111_value = paths.get("a1111_root")
    a1111_root = None if a1111_value is None else _absolute_path(a1111_value, "a1111_root")
    mode = paths.get("link_mode", "replace")
    if mode not in {"replace", "nested"}:
        raise ConfigError("link_mode must be replace or nested")

    version_data = paths.get("comfyui_versions", [])
    if not isinstance(version_data, list):
        raise ConfigError("comfyui_versions must be a list")
    versions: dict[str, Path] = {}
    for entry in version_data:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ConfigError("each ComfyUI version needs a name and path")
        name = entry["name"]
        if not name or name in versions:
            raise ConfigError(f"duplicate or empty ComfyUI version: {name!r}")
        versions[name] = _absolute_path(entry.get("path"), f"ComfyUI {name} path")
    if a1111_root is None and not versions:
        raise ConfigError("Configure at least one application: a1111_root or comfyui_versions")

    mappings: dict[str, dict[str, str]] = {}
    for app in ("a1111", "comfyui"):
        raw = mapping_data.get(app)
        if raw is None and ((app == "a1111" and a1111_root is None) or (app == "comfyui" and not versions)):
            mappings[app] = {}
            continue
        if not isinstance(raw, dict) or not raw:
            raise ConfigError(f"{app} mapping must be a nonempty mapping")
        seen: set[str] = set()
        checked: dict[str, str] = {}
        for source, target in raw.items():
            relative_parts(source, f"{app} source")
            relative_parts(target, f"{app} target")
            key = source.casefold()
            if key in seen:
                raise ConfigError(f"duplicate {app} source (ignoring case): {source}")
            seen.add(key)
            checked[source] = target
        mappings[app] = checked

    return Config(warehouse, a1111_root, versions, mappings, mode, config_dir)
