"""Atomic warehouse manifests and read-only discovery of legacy records."""

import json
from pathlib import Path
from typing import Callable

from ai_toolchain.storage.models import StorageError
from ai_toolchain.storage.ports import FileSystem


class WarehouseJournal:
    def __init__(self, fs: FileSystem, root: Path, new_id: Callable[[], str], legacy_root: Path) -> None:
        self.fs, self.root, self._new_id, self.legacy_root = fs, root, new_id, legacy_root

    def new_id(self) -> str:
        return self._new_id()

    def _path(self, migration_id: str) -> Path:
        if not migration_id or not all(c.isascii() and (c.isalnum() or c in "-_") for c in migration_id):
            raise StorageError("Invalid migration ID")
        return self.root / "journals" / f"{migration_id}.json"

    def save(self, record: dict) -> None:
        self.fs.mkdir(self.root)
        self.fs.hide(self.root)
        self.fs.write_atomic(self._path(record["id"]), json.dumps(record, indent=2).encode("utf-8"))

    def load(self, migration_id: str) -> dict:
        try:
            record = json.loads(self.fs.read_bytes(self._path(migration_id)))
        except (OSError, ValueError) as exc:
            raise StorageError(f"Cannot read warehouse migration {migration_id}: {exc}") from exc
        if record.get("schema") != 2 or record.get("id") != migration_id:
            raise StorageError(f"Invalid warehouse manifest: {migration_id}")
        return record

    def list_records(self) -> list[dict]:
        directory = self.root / "journals"
        return [self.load(path.stem) for path in sorted(directory.glob("*.json"), reverse=True)] if directory.is_dir() else []

    def legacy_records(self) -> list[dict]:
        if not self.legacy_root.is_dir():
            return []
        records = []
        for path in sorted(self.legacy_root.glob("*.json"), reverse=True):
            try:
                record = json.loads(self.fs.read_bytes(path))
                if record.get("id") != path.stem:
                    raise StorageError(f"Legacy journal ID mismatch: {path}")
                records.append(record)
            except (OSError, ValueError) as exc:
                raise StorageError(f"Cannot read legacy journal {path}: {exc}") from exc
        return records

    def archive_legacy(self, migration_id: str) -> None:
        self._path(migration_id)
        source = self.legacy_root / f"{migration_id}.json"
        target = self.root / "legacy" / source.name
        if not self.fs.exists(source):
            if self.fs.exists(target):
                return
            raise StorageError(f"Legacy journal missing: {source}")
        data = self.fs.read_bytes(source)
        if self.fs.exists(target):
            if self.fs.read_bytes(target) != data:
                raise StorageError(f"Different legacy archive: {target}")
        else:
            self.fs.write_atomic(target, data)
        if self.fs.read_bytes(target) != data:
            raise StorageError(f"Legacy archive verification failed: {target}")
        self.fs.unlink(source)
