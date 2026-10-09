from pathlib import Path

import pytest
import yaml

from ai_toolchain.config import ConfigError, load_config
from ai_toolchain.targets import select_targets


def test_checked_in_installations_and_mappings():
    config = load_config()
    assert set(config.comfyui_versions) == {"v018", "v037_cu126"}
    assert config.link_mode == "replace"
    assert config.mappings["a1111"]["embeddings"] == "Embeddings"
    assert config.mappings["comfyui"]["models/vae"] == "VAEs"
    assert len(select_targets(config, "comfyui", "v018")) == 15


def test_packaged_defaults_match_checkout():
    package = Path(__file__).resolve().parents[1] / "src" / "ai_toolchain" / "default_config"
    checkout = Path(__file__).resolve().parents[1] / "config"
    for name in ("paths.yaml", "mapping.yaml"):
        assert yaml.safe_load((package / name).read_text(encoding="utf-8")) == yaml.safe_load((checkout / name).read_text(encoding="utf-8"))


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
