from pathlib import Path

import pytest
import yaml

from ai_toolchain.config import ConfigError, load_config
from ai_toolchain.config import resolve_config_dir
from ai_toolchain.config_setup import ConfigInitializer
from ai_toolchain.storage.adapters.local_fs import LocalFileSystem
from ai_toolchain.targets import select_targets
from ai_toolchain.cli import app
from typer.testing import CliRunner


TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "ai_toolchain" / "default_config"


@pytest.fixture
def private_config(tmp_path):
    directory = tmp_path / "private-config"
    ConfigInitializer(LocalFileSystem(), TEMPLATES).initialize(directory)
    paths = {
        "warehouse_root": str(tmp_path / "warehouse"),
        "a1111_root": str(tmp_path / "a1111"),
        "comfyui_versions": [
            {"name": "primary", "path": str(tmp_path / "first-comfy")},
            {"name": "secondary", "path": str(tmp_path / "second-comfy")},
        ],
        "link_mode": "replace",
    }
    (directory / "paths.yaml").write_text(yaml.safe_dump(paths), encoding="utf-8")
    return directory


def test_user_installations_and_example_mappings(private_config):
    config = load_config(private_config)
    assert set(config.comfyui_versions) == {"primary", "secondary"}
    assert config.link_mode == "replace"
    assert config.mappings["a1111"]["embeddings"] == "Embeddings"
    assert config.mappings["comfyui"]["models/vae"] == "VAEs"
    assert len(select_targets(config, "comfyui", "primary")) == 15


def test_packaged_defaults_match_checkout():
    package = Path(__file__).resolve().parents[1] / "src" / "ai_toolchain" / "default_config"
    checkout = Path(__file__).resolve().parents[1] / "config"
    for name in ("paths.example.yaml", "mapping.example.yaml"):
        assert yaml.safe_load((package / name).read_text(encoding="utf-8")) == yaml.safe_load((checkout / name).read_text(encoding="utf-8"))


def test_configuration_search_priority(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "config").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    explicit, environment, user = (tmp_path / name for name in ("explicit", "environment", "user"))
    options = {"environ": {"AI_TOOLCHAIN_CONFIG_DIR": str(environment)}, "checkout_root": checkout, "user_config_dir": user}
    assert resolve_config_dir(explicit, **options) == explicit
    assert resolve_config_dir(**options) == environment
    assert resolve_config_dir(environ={}, checkout_root=checkout, user_config_dir=user) == checkout / "config" / "local"
    assert resolve_config_dir(environ={}, checkout_root=tmp_path / "installed", user_config_dir=user) == user


def test_missing_configuration_explains_initialization(tmp_path):
    with pytest.raises(ConfigError, match="init-config"):
        load_config(tmp_path / "missing")


def test_unedited_templates_cannot_be_used(tmp_path):
    ConfigInitializer(LocalFileSystem(), TEMPLATES).initialize(tmp_path)
    with pytest.raises(ConfigError, match="template placeholder"):
        load_config(tmp_path)


def test_initializer_preserves_existing_configuration(tmp_path):
    original = tmp_path / "paths.yaml"
    original.write_text("private settings", encoding="utf-8")
    with pytest.raises(ConfigError, match="already exists"):
        ConfigInitializer(LocalFileSystem(), TEMPLATES).initialize(tmp_path)
    assert original.read_text(encoding="utf-8") == "private settings"
    assert not (tmp_path / "mapping.yaml").exists()


def test_init_config_command_does_not_need_active_configuration(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["--config-dir", str(tmp_path), "init-config"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "paths.yaml").is_file()
    assert (tmp_path / "mapping.yaml").is_file()
    result = runner.invoke(app, ["--config-dir", str(tmp_path), "init-config"])
    assert result.exit_code == 2
    result = runner.invoke(app, ["--config-dir", str(tmp_path), "comfyui"])
    assert result.exit_code == 2
    assert "template placeholder" in result.output


@pytest.mark.parametrize("application", ["a1111", "comfyui"])
def test_configure_only_one_application(private_config, application):
    path = private_config / "paths.yaml"
    paths = yaml.safe_load(path.read_text(encoding="utf-8"))
    if application == "comfyui":
        paths["a1111_root"] = None
        disabled = "a1111"
    else:
        paths["comfyui_versions"] = []
        disabled = "comfyui"
    path.write_text(yaml.safe_dump(paths), encoding="utf-8")
    config = load_config(private_config)
    assert all(target.app == application for target in select_targets(config))
    with pytest.raises(ConfigError, match="not configured"):
        select_targets(config, disabled)


def test_requires_at_least_one_installation(private_config):
    path = private_config / "paths.yaml"
    paths = yaml.safe_load(path.read_text(encoding="utf-8"))
    paths["a1111_root"] = None
    paths["comfyui_versions"] = []
    path.write_text(yaml.safe_dump(paths), encoding="utf-8")
    with pytest.raises(ConfigError, match="at least one application"):
        load_config(private_config)


def test_legacy_warehouse_key_is_supported(private_config):
    path = private_config / "paths.yaml"
    paths = yaml.safe_load(path.read_text(encoding="utf-8"))
    warehouse = paths.pop("warehouse_root")
    paths["repo_root"] = warehouse
    path.write_text(yaml.safe_dump(paths), encoding="utf-8")
    assert load_config(private_config).warehouse == Path(warehouse)


def test_conflicting_warehouse_keys_are_rejected(private_config):
    path = private_config / "paths.yaml"
    paths = yaml.safe_load(path.read_text(encoding="utf-8"))
    paths["repo_root"] = str(private_config / "different-warehouse")
    path.write_text(yaml.safe_dump(paths), encoding="utf-8")
    with pytest.raises(ConfigError, match="disagree"):
        load_config(private_config)


@pytest.mark.parametrize("bad_path", ["../escape", "/absolute", "models\\loras", "models//loras", "C:/outside"])
def test_rejects_unsafe_mapping(tmp_path: Path, bad_path: str):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    paths = {"repo_root": str(tmp_path / "warehouse"), "a1111_root": str(tmp_path / "a1111"), "comfyui_versions": [{"name": "one", "path": str(tmp_path / "comfy")}]}
    mappings = {"a1111": {bad_path: "Lora"}, "comfyui": {"models/loras": "Lora"}}
    (config_dir / "paths.yaml").write_text(yaml.safe_dump(paths), encoding="utf-8")
    (config_dir / "mapping.yaml").write_text(yaml.safe_dump(mappings), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_dir)
