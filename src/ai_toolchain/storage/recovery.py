"""Resume interrupted migrations and restore exact original folder shape."""

from pathlib import Path

from ai_toolchain.storage.common import object_path, relative_path, verify_file
from ai_toolchain.storage.models import StorageError
from ai_toolchain.storage.ports import FileSystem, Hasher, Journal, ProcessGuard, ProgressSink


class RecoveryService:
    def __init__(self, fs: FileSystem, hasher: Hasher, journal: Journal, progress: ProgressSink, guard: ProcessGuard) -> None:
        self.fs, self.hasher, self.journal, self.progress, self.guard = fs, hasher, journal, progress, guard

    def preflight(self, record: dict) -> int:
        if record.get("schema") != 2 or record.get("status") not in {"applied", "applying", "rolling_back"}:
            raise StorageError("Migration is not recoverable")
        needed = 0
        for entry in record["entries"]:
            source, target = Path(entry["source"]), Path(entry["target"])
            restore = self.journal.root / "staging" / record["id"] / "restore" / source.name
            if self.fs.exists(source) and not self.fs.same_target(source, target):
                if entry["phase"] in {"restored", "restoring"} and self.fs.is_dir(source) and not self.fs.is_link(source) and not self.fs.exists(restore):
                    self._verify_reconstruction(entry, source)
                else:
                    raise StorageError(f"Source changed or occupied: {source}")
            for file in entry["files"]:
                obj = object_path(self.journal.root, file["sha256"])
                self.progress.stage("Verify rollback snapshot and model", file["size"] * 2)
                verify_file(self.fs, self.hasher, obj, file["sha256"], file["size"])
                verify_file(self.fs, self.hasher, Path(file["destination"]), file["sha256"], file["size"])
                self.progress.done()
                needed += file["size"]
            stage = Path(entry["stage"])
            if self.fs.exists(stage) and record["status"] != "rolling_back":
                raise StorageError(f"Interrupted stage still exists: {stage}; resume migration first")
            if self.fs.exists(restore) and record["status"] != "rolling_back":
                raise StorageError(f"Rollback stage already exists: {restore}")
        if record["entries"] and self.fs.free_bytes(self.journal.root) < needed:
            raise StorageError(f"Insufficient free space for rollback: need {needed:,} bytes")
        return needed

    def _verify_reconstruction(self, entry: dict, root: Path) -> None:
        tree = self.fs.scan(root)
        expected_files = {f["relative"] for f in entry["files"]}
        expected_links = {link["relative"]: link["target"] for link in entry["links"]}
        expected_directories = set(entry["directories"])
        if {p.relative_to(root).as_posix() for p in tree.files} != expected_files:
            raise StorageError(f"Unknown or missing restored files: {root}")
        if {p.relative_to(root).as_posix() for p in tree.directories} != expected_directories:
            raise StorageError(f"Unknown or missing restored directories: {root}")
        if {p.relative_to(root).as_posix(): self.fs.readlink(p) for p in tree.links} != expected_links:
            raise StorageError(f"Unknown or changed restored links: {root}")
        for file in entry["files"]:
            verify_file(self.fs, self.hasher, root / relative_path(file["relative"]), file["sha256"], file["size"])

    def rollback(self, record: dict) -> None:
        if record.get("status") == "rolled_back":
            return
        total = self.preflight(record)
        self.guard.assert_stopped(list({Path(e["source"]).parents[1] for e in record["entries"]}))
        record["status"] = "rolling_back"
        self.journal.save(record)
        self.progress.stage("Copy and verify rollback models", total * 2)
        for entry in reversed(record["entries"]):
            source, target = Path(entry["source"]), Path(entry["target"])
            if entry["phase"] == "restored":
                self._verify_reconstruction(entry, source)
                continue
            restore = self.journal.root / "staging" / record["id"] / "restore" / source.name
            if self.fs.is_dir(source) and not self.fs.is_link(source) and not self.fs.exists(restore):
                self._verify_reconstruction(entry, source)
                entry["phase"] = "restored"
                self.journal.save(record)
                continue
            entry["phase"] = "restoring"
            self.journal.save(record)
            self.fs.mkdir(restore)
            for directory in entry["directories"]:
                self.fs.mkdir(restore / relative_path(directory))
            for file in entry["files"]:
                obj = object_path(self.journal.root, file["sha256"])
                dest = restore / relative_path(file["relative"])
                if not self.fs.exists(dest):
                    self.fs.copy_new(obj, dest, self.progress.advance)
                verify_file(self.fs, self.hasher, dest, file["sha256"], file["size"])
            for link in entry["links"]:
                dest = restore / relative_path(link["relative"])
                if not self.fs.exists(dest):
                    self.fs.symlink(dest, Path(link["target"]))
                if self.fs.readlink(dest) != link["target"]:
                    raise StorageError(f"Restored link verification failed: {dest}")
            self._verify_reconstruction(entry, restore)
            if self.fs.exists(source) and not self.fs.same_target(source, target):
                raise StorageError(f"Active link changed during rollback: {source}")
            if self.fs.exists(source):
                self.fs.unlink(source)
            self.fs.rename(restore, source)
            entry["phase"] = "restored"
            self.journal.save(record)
        self.progress.done()
        record["status"] = "rolled_back"
        self.journal.save(record)
