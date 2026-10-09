"""Read-only link inventory and collision preflight."""

import os
from pathlib import Path

from ai_toolchain.targets import Target
from ai_toolchain.storage.models import StorageEntry, StorageFile, StoragePlan
from ai_toolchain.storage.ports import FileSystem, Hasher, ProgressSink


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


class StoragePlanner:
    def __init__(self, fs: FileSystem, hasher: Hasher, progress: ProgressSink) -> None:
        self.fs, self.hasher, self.progress = fs, hasher, progress

    def build(self, targets: list[Target], mode: str, warehouse_root: Path) -> StoragePlan:
        if mode not in {"replace", "nested"}:
            raise ValueError(f"Unknown link mode: {mode}")
        entries: list[StorageEntry] = []
        for target in targets:
            source, warehouse = target.source, target.warehouse
            if not self.fs.is_dir(warehouse):
                entries.append(StorageEntry(target, "missing_target", str(warehouse)))
            elif not self.fs.exists(source):
                entries.append(StorageEntry(target, "missing_source", str(source)))
            elif mode == "nested":
                if self.fs.is_link(source):
                    entries.append(StorageEntry(target, "switch_nested" if self.fs.same_target(source, warehouse) else "conflict"))
                elif not self.fs.is_dir(source):
                    entries.append(StorageEntry(target, "conflict", "Source is not a directory"))
                else:
                    nested = source / "WLink"
                    if not self.fs.exists(nested):
                        entries.append(StorageEntry(target, "create_nested"))
                    elif self.fs.same_target(nested, warehouse):
                        entries.append(StorageEntry(target, "noop"))
                    elif self._managed_wlink(nested, warehouse_root):
                        entries.append(StorageEntry(target, "repair_nested"))
                    else:
                        entries.append(StorageEntry(target, "conflict", f"Occupied WLink: {nested}"))
            elif self.fs.is_link(source):
                entries.append(StorageEntry(target, "noop" if self.fs.same_target(source, warehouse) else "conflict"))
            elif self.fs.is_dir(source):
                entry = StorageEntry(target, "migrate")
                tree = self.fs.scan(source)
                entry.directories = [p.relative_to(source).as_posix() for p in tree.directories]
                for link in tree.links:
                    if link == source / "WLink" and self._managed_wlink(link, warehouse_root):
                        entry.links.append(("WLink", self.fs.readlink(link)))
                    else:
                        entry.action, entry.detail = "conflict", f"Unexpected link: {link}"
                for path in tree.files:
                    rel = path.relative_to(source)
                    entry.files.append(StorageFile(rel.as_posix(), path, warehouse / rel, self.fs.size(path)))
                entries.append(entry)
            else:
                entries.append(StorageEntry(target, "conflict", "Source is not a directory"))
        if mode == "replace":
            self._collisions(entries)
        return StoragePlan(mode, entries)

    def _managed_wlink(self, path: Path, warehouse: Path) -> bool:
        if path.name != "WLink" or not self.fs.is_symlink(path):
            return False
        value = self.fs.readlink(path)
        if os.name == "nt" and value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif os.name == "nt" and value.startswith("\\\\?\\"):
            value = value[4:]
        raw = Path(value)
        old = raw if raw.is_absolute() else path.parent / raw
        return _key(old.parent) == _key(warehouse)

    def _collisions(self, entries: list[StorageEntry]) -> None:
        planned: dict[str, Path] = {}
        for entry in entries:
            if entry.action != "migrate":
                continue
            target = entry.target.warehouse
            existing = {p.relative_to(target).as_posix().casefold(): p for p in self.fs.scan(target).files}
            for file in entry.files:
                key = file.relative.casefold()
                dest_key = _key(target) + "/" + key
                old = existing.get(key) or planned.get(dest_key)
                if old is None and self.fs.exists(file.destination):
                    old = file.destination
                if old is not None:
                    if self.fs.is_link(old) or not self.fs.is_file(old) or self.fs.size(old) != file.size:
                        entry.action, entry.detail = "conflict", f"Different-content collision: {file.destination}"
                        break
                    self.progress.stage("Hashing collision", file.size * 2)
                    left, right = self.hasher.digest(file.source), self.hasher.digest(old)
                    self.progress.done()
                    if left != right:
                        entry.action, entry.detail = "conflict", f"Different-content collision: {file.destination}"
                        break
                    file.action, file.digest = "duplicate", left
                else:
                    planned[dest_key] = file.source
                parent = file.destination.parent
                while parent != target and parent != parent.parent:
                    if self.fs.exists(parent) and (self.fs.is_link(parent) or not self.fs.is_dir(parent)):
                        entry.action, entry.detail = "conflict", f"Destination parent conflict: {parent}"
                        break
                    parent = parent.parent
                if entry.action == "conflict":
                    break
