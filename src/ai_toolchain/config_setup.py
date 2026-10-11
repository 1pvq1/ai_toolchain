"""Create private configuration from the portable packaged examples."""

from pathlib import Path

from .config import ConfigError
from .storage.ports import FileSystem


class ConfigInitializer:
    def __init__(self, fs: FileSystem, template_dir: Path) -> None:
        self.fs, self.template_dir = fs, template_dir

    def initialize(self, directory: Path) -> list[Path]:
        templates = {
            "paths.yaml": self.template_dir / "paths.example.yaml",
            "mapping.yaml": self.template_dir / "mapping.example.yaml",
        }
        destinations = [directory / name for name in templates]
        for destination in destinations:
            if self.fs.exists(destination):
                raise ConfigError(f"Local configuration already exists: {destination}; existing files are preserved")
        for template in templates.values():
            if not self.fs.is_file(template):
                raise ConfigError(f"Configuration template is missing: {template}")
        self.fs.mkdir(directory)
        for name, template in templates.items():
            self.fs.copy_new(template, directory / name, lambda _: None)
        return destinations
