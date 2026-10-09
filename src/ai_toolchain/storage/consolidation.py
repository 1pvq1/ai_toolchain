"""Convert adjacent legacy backups into verified warehouse snapshots."""

from pathlib import Path

from ai_toolchain.storage.common import object_path, relative_path, remove_empty_tree, snapshot, verify_file
from ai_toolchain.storage.models import StorageError
from ai_toolchain.storage.ports import FileSystem, Hasher, Journal, ProcessGuard, ProgressSink


class ConsolidationService:
    def __init__(self, fs: FileSystem, hasher: Hasher, journal: Journal, progress: ProgressSink, guard: ProcessGuard, warehouse_root: Path, application_roots: list[Path]) -> None:
        self.fs, self.hasher, self.journal, self.progress, self.guard = fs, hasher, journal, progress, guard
        self.warehouse_root, self.application_roots = warehouse_root, application_roots

    def _safe_paths(self, source: Path, target: Path, backup: Path) -> None:
        root = self.warehouse_root.resolve(strict=False)
        if not target.resolve(strict=False).is_relative_to(root) or target.resolve(strict=False) == root:
            raise StorageError(f"Target escapes warehouse: {target}")
        if backup.parent != source.parent or not backup.name.startswith(source.name + ".ai-toolchain-backup-"):
            raise StorageError(f"Backup is not adjacent to source: {backup}")
        if not any(source.parent.resolve(strict=False).is_relative_to(app.resolve(strict=False)) for app in self.application_roots):
            raise StorageError(f"Source escapes configured applications: {source}")

    def _legacy(self, migration_id: str) -> dict:
        matches = [r for r in self.journal.legacy_records() if r.get("id") == migration_id]
        if len(matches) != 1 or matches[0].get("status") != "applied" or matches[0].get("mode") != "replace":
            raise StorageError(f"No applied legacy migration: {migration_id}")
        return matches[0]

    def preview(self, migration_id: str) -> dict:
        """Validate every active link, backup item and journal hash before writing."""
        if not migration_id or not all(c.isascii() and (c.isalnum() or c in "-_") for c in migration_id):
            raise StorageError("Invalid migration ID")
        current = self.journal.load(migration_id) if self.fs.exists(self.journal.root / "journals" / f"{migration_id}.json") else None
        if current is not None:
            if current.get("origin") != "legacy":
                raise StorageError(f"Not a consolidation: {migration_id}")
            self._validate_resume(current)
            return current
        legacy = self._legacy(migration_id)
        entries = []
        for index, old in enumerate(legacy["entries"]):
            source, target, backup = (Path(old[key]) for key in ("source", "target", "backup"))
            self._safe_paths(source, target, backup)
            stage = self.journal.root / "staging" / migration_id / str(index)
            if not self.fs.is_symlink(source) or not self.fs.same_target(source, target):
                raise StorageError(f"Active link changed: {source}")
            if not self.fs.is_dir(backup) or self.fs.is_link(backup):
                raise StorageError(f"Backup missing or changed: {backup}")
            if not self.fs.same_volume(backup, target):
                raise StorageError(f"Backup and warehouse differ in volume: {backup}")
            tree = self.fs.scan(backup)
            expected = {file["relative"].casefold(): file for file in old["files"] if file["action"] == "duplicate"}
            actual = {file.relative_to(backup).as_posix().casefold(): file for file in tree.files}
            if len(expected) != sum(file["action"] == "duplicate" for file in old["files"]) or set(actual) != set(expected) or len(actual) != len(tree.files):
                raise StorageError(f"Unknown or missing backup files: {backup}")
            links = []
            for link in tree.links:
                if link != backup / "WLink" or not self.fs.is_symlink(link):
                    raise StorageError(f"Unknown backup link: {link}")
                if not link.resolve(strict=False).is_relative_to(self.warehouse_root.resolve(strict=False)):
                    raise StorageError(f"Backup WLink escapes warehouse: {link}")
                links.append({"relative": "WLink", "target": self.fs.readlink(link)})
            files = []
            bytes_to_hash = sum(self.fs.size(Path(f["destination"])) for f in old["files"] if self.fs.is_file(Path(f["destination"])))
            bytes_to_hash += sum(self.fs.size(p) for p in tree.files)
            self.progress.stage("Validate legacy backup and warehouse", bytes_to_hash)
            for file in old["files"]:
                relative = relative_path(file["relative"])
                destination = Path(file["destination"])
                if destination != target / relative:
                    raise StorageError(f"Journal destination escapes target: {destination}")
                digest = file["sha256"]
                size = self.fs.size(destination) if self.fs.is_file(destination) and not self.fs.is_link(destination) else -1
                verify_file(self.fs, self.hasher, destination, digest)
                if file["action"] == "duplicate":
                    duplicate = actual[relative.as_posix().casefold()]
                    if duplicate.relative_to(backup) != relative:
                        raise StorageError(f"Case-colliding backup file: {duplicate}")
                    verify_file(self.fs, self.hasher, duplicate, digest, size)
                files.append({"relative": relative.as_posix(), "destination": str(destination), "size": size, "sha256": digest, "action": file["action"], "phase": "planned"})
            self.progress.done()
            entries.append({
                "label": old["label"], "source": str(source), "target": str(target),
                "stage": str(stage), "backup": str(backup), "phase": "planned",
                "files": files,
                "directories": [p.relative_to(backup).as_posix() for p in tree.directories],
                "links": links,
            })
        return {"schema": 2, "id": migration_id, "origin": "legacy", "status": "planned", "entries": entries}

    def _validate_resume(self, manifest: dict, selected: dict | None = None, verify_models: bool = True) -> None:
        for entry in [selected] if selected is not None else manifest["entries"]:
            source, target, backup, stage = (Path(entry[key]) for key in ("source", "target", "backup", "stage"))
            self._safe_paths(source, target, backup)
            if not stage.resolve(strict=False).is_relative_to((self.journal.root / "staging" / manifest["id"]).resolve(strict=False)):
                raise StorageError(f"Stage escapes warehouse state: {stage}")
            if not self.fs.is_symlink(source) or not self.fs.same_target(source, target):
                raise StorageError(f"Active link changed: {source}")
            if self.fs.exists(backup) and self.fs.exists(stage):
                raise StorageError(f"Both backup and stage exist: {backup}")
            if entry["phase"] == "cleaned" and (self.fs.exists(backup) or self.fs.exists(stage)):
                raise StorageError(f"Cleaned backup reappeared: {backup}")
            if not self.fs.exists(backup) and not self.fs.exists(stage) and entry["phase"] not in {"staged", "cleaning", "cleaned"}:
                raise StorageError(f"Backup and stage missing: {backup}")
            for file in entry["files"]:
                model = Path(file["destination"])
                if verify_models:
                    verify_file(self.fs, self.hasher, model, file["sha256"], file["size"])
                obj = object_path(self.journal.root, file["sha256"])
                if self.fs.exists(obj):
                    if verify_models:
                        verify_file(self.fs, self.hasher, obj, file["sha256"], file["size"])
                elif not self.fs.exists(backup) and not self.fs.exists(stage):
                    raise StorageError(f"Snapshot missing after cleanup: {obj}")
            present = stage if self.fs.exists(stage) else backup
            if self.fs.exists(present):
                tree = self.fs.scan(present)
                expected = {f["relative"].casefold() for f in entry["files"] if f["action"] == "duplicate"}
                found = {p.relative_to(present).as_posix().casefold() for p in tree.files}
                if not found.issubset(expected):
                    raise StorageError(f"Unknown files in staged backup: {present}")
                expected_links = {p["relative"] for p in entry["links"]}
                found_links = {p.relative_to(present).as_posix() for p in tree.links}
                if not found_links.issubset(expected_links):
                    raise StorageError(f"Unknown links in staged backup: {present}")
                expected_dirs = set(entry["directories"])
                found_dirs = {p.relative_to(present).as_posix() for p in tree.directories}
                if not found_dirs.issubset(expected_dirs):
                    raise StorageError(f"Unknown directories in staged backup: {present}")
                if present == backup or entry["phase"] not in {"cleaning", "cleaned"}:
                    if found != expected or found_links != expected_links or found_dirs != expected_dirs:
                        raise StorageError(f"Backup inventory changed: {present}")
                for path in tree.files:
                    file = next(f for f in entry["files"] if f["relative"].casefold() == path.relative_to(present).as_posix().casefold())
                    verify_file(self.fs, self.hasher, path, file["sha256"], file["size"])

    def apply_many(self, migration_ids: list[str]) -> list[dict]:
        if len(set(migration_ids)) != len(migration_ids):
            raise StorageError("Duplicate migration IDs")
        manifests = [self.preview(value) for value in migration_ids]
        roots = list({str(Path(e["source"]).parents[1]): Path(e["source"]).parents[1] for m in manifests for e in m["entries"]}.values())
        self.guard.assert_stopped(roots)
        # Preflight all selected migrations and required hardlink support before the first cleanup.
        for manifest in manifests:
            for entry in manifest["entries"]:
                if not self.fs.same_volume(Path(entry["target"]), self.journal.root.parent):
                    raise StorageError(f"State and target are on different volumes: {entry['target']}")
        for manifest in manifests:
            self._apply(manifest)
        return manifests

    def _apply(self, manifest: dict) -> None:
        if manifest["status"] == "applied":
            self.journal.archive_legacy(manifest["id"])
            return
        self.journal.save(manifest)
        for entry in manifest["entries"]:
            if entry["phase"] == "cleaned":
                continue
            backup, stage = Path(entry["backup"]), Path(entry["stage"])
            if not self.fs.exists(backup) and not self.fs.exists(stage) and entry["phase"] in {"staged", "cleaning"}:
                self._validate_resume(manifest, entry)
                entry["phase"] = "cleaned"
                self.journal.save(manifest)
                continue
            if entry["phase"] != "cleaning":
                self.progress.stage("Create and verify snapshots", sum(f["size"] * 2 for f in entry["files"]))
                for file in entry["files"]:
                    file["phase"] = "snapshotting"
                    self.journal.save(manifest)
                    snapshot(self.fs, self.hasher, self.journal.root, Path(file["destination"]), file["sha256"], file["size"])
                    file["phase"] = "snapshotted"
                    self.journal.save(manifest)
                self.progress.done()
                entry["phase"] = "snapshotted"
                self.journal.save(manifest)
                if self.fs.exists(backup):
                    entry["phase"] = "staging"
                    self.journal.save(manifest)
                    self.fs.mkdir(stage.parent)
                    self.fs.rename(backup, stage)
                elif not self.fs.exists(stage):
                    raise StorageError(f"Backup staging missing: {backup}")
                entry["phase"] = "staged"
                self.journal.save(manifest)
                self._validate_resume(manifest, entry, False)
                entry["phase"] = "cleaning"
                self.journal.save(manifest)
            redundant = sum(f["size"] for f in entry["files"] if f["action"] == "duplicate")
            if redundant:
                self.progress.stage("Consolidate duplicate backup bytes", redundant)
            for file in entry["files"]:
                if file["action"] != "duplicate":
                    continue
                item = stage / relative_path(file["relative"])
                if self.fs.exists(item):
                    file["phase"] = "retiring"
                    self.journal.save(manifest)
                    verify_file(self.fs, self.hasher, item, file["sha256"], file["size"])
                    self.fs.unlink(item)
                file["phase"] = "retired"
                self.journal.save(manifest)
            if redundant:
                self.progress.done()
            for link in entry["links"]:
                item = stage / relative_path(link["relative"])
                if self.fs.exists(item):
                    if not self.fs.is_symlink(item) or self.fs.readlink(item) != link["target"]:
                        raise StorageError(f"Staged link changed: {item}")
                    self.fs.unlink(item)
            remove_empty_tree(self.fs, stage, entry["directories"])
            entry["phase"] = "cleaned"
            self.journal.save(manifest)
        manifest["status"] = "archiving"
        self.journal.save(manifest)
        self.journal.archive_legacy(manifest["id"])
        manifest["status"] = "applied"
        self.journal.save(manifest)
