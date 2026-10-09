"""Pure storage plans and versioned journal shapes."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from ai_toolchain.targets import Target


class StorageError(RuntimeError):
    """Preflight, migration, or recovery could not proceed safely."""


@dataclass
class StorageFile:
    relative: str
    source: Path
    destination: Path
    size: int
    action: Literal["move", "duplicate"] = "move"
    digest: str | None = None


@dataclass
class StorageEntry:
    target: Target
    action: str
    detail: str = ""
    files: list[StorageFile] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class StoragePlan:
    mode: str
    entries: list[StorageEntry]

    @property
    def blocked(self) -> bool:
        return any(item.action == "conflict" for item in self.entries)


class FileRecord(TypedDict):
    relative: str
    destination: str
    size: int
    sha256: str
    action: Literal["move", "duplicate"]
    phase: NotRequired[str]


class LinkRecord(TypedDict):
    relative: str
    target: str


class EntryRecord(TypedDict):
    label: str
    source: str
    target: str
    stage: str
    backup: NotRequired[str]
    phase: str
    files: list[FileRecord]
    directories: list[str]
    links: list[LinkRecord]


class ManifestV2(TypedDict):
    schema: Literal[2]
    id: str
    origin: Literal["new", "legacy"]
    status: Literal["planned", "applying", "archiving", "applied", "rolling_back", "rolled_back"]
    entries: list[EntryRecord]
