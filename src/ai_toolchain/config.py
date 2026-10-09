"""Load and validate the two user-editable YAML files."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping

import yaml


class ConfigError(ValueError):
    """The configuration cannot be used safely."""


@dataclass(frozen=True)
class Config:
    warehouse: Path
    a1111_root: Path
    comfyui_versions: Mapping[str, Path]
    mappings: Mapping[str, Mapping[str, str]]
    link_mode: str
    config_dir: Path


def _absolute_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a nonempty absolute path")
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


def load_config(config_dir: Path | None = None) -> Config:
    if config_dir is None:
        checkout_config = Path(__file__).resolve().parents[2] / "config"
        config_dir = checkout_config if (checkout_config / "paths.yaml").is_file() else Path(__file__).parent / "default_config"
    paths = _read_yaml(config_dir / "paths.yaml")
    mapping_data = _read_yaml(config_dir / "mapping.yaml")
    warehouse = _absolute_path(paths.get("repo_root"), "repo_root")
    a1111_root = _absolute_path(paths.get("a1111_root"), "a1111_root")
    mode = paths.get("link_mode", "replace")
    if mode not in {"replace", "nested"}:
        raise ConfigError("link_mode must be replace or nested")

    version_data = paths.get("comfyui_versions")
    if not isinstance(version_data, list) or not version_data:
        raise ConfigError("comfyui_versions must be a nonempty list")
    versions: dict[str, Path] = {}
    for entry in version_data:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ConfigError("each ComfyUI version needs a name and path")
        name = entry["name"]
        if not name or name in versions:
            raise ConfigError(f"duplicate or empty ComfyUI version: {name!r}")
        versions[name] = _absolute_path(entry.get("path"), f"ComfyUI {name} path")

    mappings: dict[str, dict[str, str]] = {}
    for app in ("a1111", "comfyui"):
        raw = mapping_data.get(app)
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
