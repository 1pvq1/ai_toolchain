"""Warehouse-staged whole-folder migration and nested WLink application."""

from pathlib import Path

from ai_toolchain.storage.common import relative_path, remove_empty_tree, snapshot, verify_file
from ai_toolchain.storage.models import StorageError, StoragePlan
from ai_toolchain.storage.ports import FileSystem, Hasher, Journal, ProcessGuard, ProgressSink
from ai_toolchain.storage.recovery import RecoveryService


class MigrationService:
    def __init__(self, fs: FileSystem, hasher: Hasher, journal: Journal, progress: ProgressSink, guard: ProcessGuard, recovery: RecoveryService) -> None:
        self.fs, self.hasher, self.journal, self.progress, self.guard = fs, hasher, journal, progress, guard
        self.recovery = recovery

    def apply(self, plan: StoragePlan) -> str | None:
        if plan.blocked:
            raise StorageError("Preflight has conflicts; nothing was applied")
        work = [entry for entry in plan.entries if entry.action in {"migrate", "create_nested", "repair_nested", "switch_nested"}]
        self.guard.assert_stopped(list({entry.target.root for entry in work}))
        if plan.mode == "nested":
            self._nested(work)
            return None
        migration = [entry for entry in work if entry.action == "migrate"]
        if not migration:
            return None
        existing_records = self.journal.list_records()
        pending_sources = {
            entry["source"]
            for old in existing_records if old.get("status") not in {"applied", "rolled_back"}
            for entry in old["entries"]
        }
        if any(str(entry.target.source) in pending_sources for entry in migration):
            raise StorageError("An interrupted migration affects a selected source; resume it first")
        migration_id = self.journal.new_id()
        if migration_id in {old["id"] for old in existing_records}:
            raise StorageError(f"Migration ID already exists: {migration_id}")
        entries = []
        # Hash and check every selected source before any rename.
        for index, item in enumerate(migration):
            target = item.target
            if not self.fs.same_volume(target.source, target.warehouse):
                raise StorageError(f"Source and warehouse must share a volume: {target.source}")
            if not self.fs.same_volume(target.source, self.journal.root.parent):
                raise StorageError(f"Warehouse state and source must share a volume: {target.source}")
            tree = self.fs.scan(target.source)
            if {p.relative_to(target.source).as_posix() for p in tree.files} != {f.relative for f in item.files}:
                raise StorageError(f"Source file inventory changed: {target.source}")
            if {p.relative_to(target.source).as_posix() for p in tree.directories} != set(item.directories):
                raise StorageError(f"Source directories changed: {target.source}")
            if {(p.relative_to(target.source).as_posix(), self.fs.readlink(p)) for p in tree.links} != set(item.links):
                raise StorageError(f"Source links changed: {target.source}")
            stage = self.journal.root / "staging" / migration_id / str(index)
            if self.fs.exists(stage):
                raise StorageError(f"Stage already exists: {stage}")
            files = []
            for file in item.files:
                self.progress.stage("Hash source model", file.size)
                digest = self.hasher.digest(file.source)
                self.progress.done()
                if file.action == "duplicate":
                    verify_file(self.fs, self.hasher, file.destination, digest, file.size)
                elif self.fs.exists(file.destination):
                    raise StorageError(f"Destination appeared after preflight: {file.destination}")
                files.append({"relative": file.relative, "destination": str(file.destination), "size": file.size, "sha256": digest, "action": file.action, "phase": "planned"})
            entries.append({"label": target.label, "source": str(target.source), "target": str(target.warehouse), "stage": str(stage), "phase": "planned", "files": files, "directories": item.directories, "links": [{"relative": path, "target": value} for path, value in item.links]})
        record = {"schema": 2, "id": migration_id, "origin": "new", "status": "applying", "entries": entries}
        self.journal.save(record)
        self.resume(record)
        return migration_id

    def resume(self, record: dict) -> None:
        if record.get("origin") != "new" or record.get("status") not in {"applying", "applied"}:
            raise StorageError("Not a resumable new migration")
        if record["status"] == "applied":
            return
        self.guard.assert_stopped(list({Path(e["source"]).parents[1] for e in record["entries"]}))
        for entry in record["entries"]:
            source, target, stage = (Path(entry[key]) for key in ("source", "target", "stage"))
            if self.fs.is_symlink(source) and self.fs.same_target(source, target):
                if self.fs.exists(stage):
                    raise StorageError(f"Both active link and stage exist: {source}")
                entry["phase"] = "linked"
                self.journal.save(record)
                continue
            if not self.fs.exists(source) and not self.fs.exists(stage) and entry["phase"] in {"transferred", "cleaned", "linking"}:
                for file in entry["files"]:
                    verify_file(self.fs, self.hasher, Path(file["destination"]), file["sha256"], file["size"])
                self.fs.symlink(source, target)
                if not self.fs.same_target(source, target):
                    raise StorageError(f"Link verification failed: {source}")
                entry["phase"] = "linked"
                self.journal.save(record)
                continue
            if self.fs.exists(source) and not self.fs.exists(stage):
                if self.fs.is_link(source) or not self.fs.is_dir(source):
                    raise StorageError(f"Source changed: {source}")
                entry["phase"] = "staging"
                self.journal.save(record)
                self.fs.mkdir(stage.parent)
                self.fs.rename(source, stage)
            elif self.fs.exists(source) or not self.fs.is_dir(stage):
                raise StorageError(f"Unexpected stage/source state: {source}")
            entry["phase"] = "staged"
            self.journal.save(record)
            self._transfer(entry, record)
            self._clean_stage(entry, record)
            entry["phase"] = "linking"
            self.journal.save(record)
            self.fs.symlink(source, target)
            if not self.fs.same_target(source, target):
                raise StorageError(f"Link verification failed: {source}")
            entry["phase"] = "linked"
            self.journal.save(record)
        record["status"] = "applied"
        self.journal.save(record)

    def _transfer(self, entry: dict, record: dict) -> None:
        stage = Path(entry["stage"])
        self.progress.stage("Stage and verify warehouse models", sum(f["size"] * (4 if self.fs.exists(stage / relative_path(f["relative"])) else 3) for f in entry["files"]))
        for file in entry["files"]:
            origin = stage / relative_path(file["relative"])
            destination = Path(file["destination"])
            digest, size = file["sha256"], file["size"]
            if self.fs.exists(origin):
                file["phase"] = "publishing"
                self.journal.save(record)
                verify_file(self.fs, self.hasher, origin, digest, size)
                if self.fs.exists(destination):
                    verify_file(self.fs, self.hasher, destination, digest, size)
                else:
                    if file["action"] == "duplicate":
                        raise StorageError(f"Duplicate destination disappeared: {destination}")
                    self.fs.hardlink(origin, destination)
                    verify_file(self.fs, self.hasher, destination, digest, size)
                file["phase"] = "published"
                self.journal.save(record)
                file["phase"] = "snapshotting"
                self.journal.save(record)
                snapshot(self.fs, self.hasher, self.journal.root, destination, digest, size)
                file["phase"] = "snapshotted"
                self.journal.save(record)
                self.fs.unlink(origin)
            else:
                verify_file(self.fs, self.hasher, destination, digest, size)
                snapshot(self.fs, self.hasher, self.journal.root, destination, digest, size)
            file["phase"] = "retired"
            self.journal.save(record)
        self.progress.done()
        entry["phase"] = "transferred"
        self.journal.save(record)

    def _clean_stage(self, entry: dict, record: dict) -> None:
        stage = Path(entry["stage"])
        tree = self.fs.scan(stage)
        if tree.files:
            raise StorageError(f"Unknown files in stage: {stage}")
        expected_links = {p["relative"]: p["target"] for p in entry["links"]}
        for path in tree.links:
            key = path.relative_to(stage).as_posix()
            if key not in expected_links or not self.fs.is_symlink(path) or self.fs.readlink(path) != expected_links[key]:
                raise StorageError(f"Unknown link in stage: {path}")
            self.fs.unlink(path)
        remove_empty_tree(self.fs, stage, entry["directories"])
        entry["phase"] = "cleaned"
        self.journal.save(record)

    def _nested(self, entries: list) -> None:
        switches = {str(entry.target.source) for entry in entries if entry.action == "switch_nested"}
        records: dict[str, dict] = {}
        if switches:
            for record in self.journal.list_records():
                if record.get("status") != "applied":
                    continue
                covered = switches.intersection(e["source"] for e in record["entries"])
                if covered:
                    if covered != {e["source"] for e in record["entries"]}:
                        raise StorageError(f"Select all folders from migration {record['id']} before switching modes")
                    records[record["id"]] = record
                    switches -= covered
            if switches:
                raise StorageError(f"No warehouse manifest for whole-folder link: {sorted(switches)[0]}")
            required = sum(self.recovery.preflight(record) for record in records.values())
            if required > self.fs.free_bytes(self.journal.root):
                raise StorageError(f"Insufficient free space for nested switch: need {required:,} bytes")
            for record in records.values():
                self.recovery.rollback(record)
        for entry in entries:
            source, target = entry.target.source, entry.target.warehouse
            nested = source / "WLink"
            if entry.action in {"repair_nested", "switch_nested"} and self.fs.exists(nested):
                if self.fs.same_target(nested, target):
                    continue
                if not self.fs.is_symlink(nested):
                    raise StorageError(f"Occupied WLink: {nested}")
                old = self.fs.readlink(nested)
                self.fs.unlink(nested)
                try:
                    self.fs.symlink(nested, target)
                except OSError:
                    self.fs.symlink(nested, Path(old))
                    raise
            elif entry.action in {"create_nested", "switch_nested"}:
                self.fs.symlink(nested, target)
            if not self.fs.same_target(nested, target):
                raise StorageError(f"Nested link verification failed: {nested}")
