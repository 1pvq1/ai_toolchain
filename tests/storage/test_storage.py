"""Integration coverage for warehouse staging, consolidation, and recovery."""

import hashlib
import json
from types import SimpleNamespace
import pytest

from ai_toolchain.storage.adapters.local_fs import LocalFileSystem
from ai_toolchain.storage.adapters.process_guard import PsutilProcessGuard
from ai_toolchain.storage.adapters.rich_progress import RichProgressSink
from ai_toolchain.storage.adapters.sha256 import Sha256Hasher
from ai_toolchain.storage.adapters.warehouse_journal import WarehouseJournal
from ai_toolchain.storage.consolidation import ConsolidationService
from ai_toolchain.storage.migration import MigrationService
from ai_toolchain.storage.models import StorageError
from ai_toolchain.storage.planner import StoragePlanner
from ai_toolchain.storage.recovery import RecoveryService
from ai_toolchain.targets import Target


class Progress:
    def __init__(self):
        self.stages = []
        self.bytes = 0

    def stage(self, name, total_bytes):
        self.stages.append((name, total_bytes))

    def advance(self, amount):
        self.bytes += amount

    def done(self):
        pass

    def message(self, value):
        pass


class Guard:
    def assert_stopped(self, roots):
        pass


def setup(tmp_path, fs=None):
    fs = fs or LocalFileSystem()
    progress = Progress()
    hasher = Sha256Hasher(progress)
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    journal = WarehouseJournal(fs, warehouse / ".ai_toolchain", lambda: "migration-1", legacy)
    return fs, progress, hasher, warehouse, legacy, journal, Guard()


def migration_service(fs, hasher, journal, progress, guard):
    recovery = RecoveryService(fs, hasher, journal, progress, guard)
    return MigrationService(fs, hasher, journal, progress, guard, recovery)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def legacy_fixture(tmp_path, fs=None):
    fs, progress, hasher, warehouse, legacy, journal, guard = setup(tmp_path, fs)
    app = tmp_path / "app" / "ComfyUI"
    models = app / "models"
    models.mkdir(parents=True)
    category = warehouse / "checkpoints"
    category.mkdir()
    (category / "same.bin").write_bytes(b"same content")
    (category / "moved.bin").write_bytes(b"moved content")
    source = models / "checkpoints"
    source.symlink_to(category, target_is_directory=True)
    backup = models / "checkpoints.ai-toolchain-backup-legacy"
    backup.mkdir()
    (backup / "same.bin").write_bytes(b"same content")
    (backup / "empty").mkdir()
    (backup / "WLink").symlink_to(category, target_is_directory=True)
    files = [
        {"relative": name, "destination": str(category / name), "action": action, "sha256": digest(content), "phase": "moved"}
        for name, content, action in [("same.bin", b"same content", "duplicate"), ("moved.bin", b"moved content", "move")]
    ]
    old = {"id": "legacy-1", "mode": "replace", "status": "applied", "entries": [{"label": "comfyui/test:models/checkpoints", "source": str(source), "target": str(category), "backup": str(backup), "phase": "linked", "files": files}]}
    (legacy / "legacy-1.json").write_text(json.dumps(old), encoding="utf-8")
    return fs, progress, hasher, warehouse, legacy, journal, guard, source, backup, category


def test_consolidate_and_reconstruct_exact_original(tmp_path):
    fs, progress, hasher, warehouse, legacy, journal, guard, source, backup, category = legacy_fixture(tmp_path)
    service = ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]])
    service.preview("legacy-1")
    assert backup.is_dir()
    service.apply_many(["legacy-1"])
    assert source.is_symlink() and source.resolve() == category.resolve()
    assert not backup.exists()
    assert not (legacy / "legacy-1.json").exists()
    assert (journal.root / "legacy" / "legacy-1.json").exists()
    assert (journal.root / "objects" / digest(b"same content")[:2] / digest(b"same content")).stat().st_nlink >= 2
    assert journal.load("legacy-1")["status"] == "applied"
    RecoveryService(fs, hasher, journal, progress, guard).rollback(journal.load("legacy-1"))
    assert source.is_dir() and not source.is_symlink()
    assert (source / "same.bin").read_bytes() == b"same content"
    assert (source / "moved.bin").read_bytes() == b"moved content"
    assert (source / "empty").is_dir()
    assert (source / "WLink").is_symlink()
    assert (category / "same.bin").exists()


def test_unknown_backup_file_refuses_consolidation(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard, source, backup, _ = legacy_fixture(tmp_path)
    (backup / "unknown.bin").write_bytes(b"unknown")
    with pytest.raises(StorageError, match="Unknown"):
        ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]]).preview("legacy-1")
    assert backup.is_dir()


def test_changed_model_refuses_consolidation(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard, source, backup, category = legacy_fixture(tmp_path)
    (category / "moved.bin").write_bytes(b"changed")
    with pytest.raises(StorageError, match="changed"):
        ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]]).preview("legacy-1")
    assert backup.is_dir()


class FailHardlinkFs(LocalFileSystem):
    def hardlink(self, source, target):
        raise OSError("hardlinks unavailable")


def test_hardlink_failure_preserves_legacy_backup(tmp_path):
    fs, progress, hasher, warehouse, legacy, journal, guard, source, backup, _ = legacy_fixture(tmp_path, FailHardlinkFs())
    service = ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]])
    with pytest.raises(OSError, match="hardlinks unavailable"):
        service.apply_many(["legacy-1"])
    assert backup.is_dir()
    assert source.is_symlink()
    assert (legacy / "legacy-1.json").is_file()


class FailOnceFs(LocalFileSystem):
    def __init__(self):
        self.failed = False

    def unlink(self, path):
        if path.name == "same.bin" and not self.failed:
            self.failed = True
            super().unlink(path)
            raise OSError("injected failure")
        super().unlink(path)


def test_interrupted_consolidation_resumes(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard, source, backup, category = legacy_fixture(tmp_path, FailOnceFs())
    service = ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]])
    with pytest.raises(OSError, match="injected"):
        service.apply_many(["legacy-1"])
    assert not backup.exists()
    assert source.is_symlink()
    service.apply_many(["legacy-1"])
    service.apply_many(["legacy-1"])
    assert journal.load("legacy-1")["status"] == "applied"
    assert source.resolve() == category.resolve()


def test_interrupted_legacy_archive_resumes(tmp_path):
    fs, progress, hasher, warehouse, legacy, journal, guard, source, backup, _ = legacy_fixture(tmp_path)
    service = ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]])
    original_archive = journal.archive_legacy
    calls = 0

    def fail_once(migration_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected archive failure")
        return original_archive(migration_id)

    journal.archive_legacy = fail_once
    with pytest.raises(OSError, match="injected archive failure"):
        service.apply_many(["legacy-1"])
    assert journal.load("legacy-1")["status"] == "archiving"
    assert not backup.exists()
    assert (legacy / "legacy-1.json").exists()
    service.apply_many(["legacy-1"])
    assert journal.load("legacy-1")["status"] == "applied"
    assert not (legacy / "legacy-1.json").exists()


def test_new_migration_stages_in_warehouse(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard = setup(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    source.mkdir()
    (source / "sub").mkdir()
    (source / "sub" / "local.bin").write_bytes(b"local")
    (source / "empty").mkdir()
    category = warehouse / "category"
    category.mkdir()
    target = Target("comfyui", "test", app, "models", "category", source, category)
    plan = StoragePlanner(fs, hasher, progress).build([target], "replace", warehouse)
    migration_id = migration_service(fs, hasher, journal, progress, guard).apply(plan)
    assert migration_id == "migration-1"
    assert source.is_symlink()
    assert not list(app.glob("*.ai-toolchain-backup-*"))
    assert (category / "sub" / "local.bin").read_bytes() == b"local"
    assert StoragePlanner(fs, hasher, progress).build([target], "replace", warehouse).entries[0].action == "noop"
    RecoveryService(fs, hasher, journal, progress, guard).rollback(journal.load(migration_id))
    assert (source / "sub" / "local.bin").read_bytes() == b"local"
    assert (source / "empty").is_dir()


def test_different_content_collision_blocks_whole_plan(tmp_path):
    fs, progress, hasher, warehouse, _, _, _ = setup(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    source.mkdir()
    (source / "same.bin").write_bytes(b"local")
    category = warehouse / "category"
    category.mkdir()
    (category / "same.bin").write_bytes(b"other")
    target = Target("comfyui", "test", app, "models", "category", source, category)
    plan = StoragePlanner(fs, hasher, progress).build([target], "replace", warehouse)
    assert plan.blocked
    assert source.is_dir() and not source.is_symlink()


def test_case_insensitive_cross_application_collision(tmp_path):
    fs, progress, hasher, warehouse, _, _, _ = setup(tmp_path)
    category = warehouse / "category"
    category.mkdir()
    targets = []
    for name, filename in (("first", "SAME.bin"), ("second", "same.bin")):
        app = tmp_path / name
        app.mkdir()
        source = app / "models"
        source.mkdir()
        (source / filename).write_bytes(b"same")
        targets.append(Target("comfyui", name, app, "models", "category", source, category))
    plan = StoragePlanner(fs, hasher, progress).build(targets, "replace", warehouse)
    assert not plan.blocked
    assert [f.action for e in plan.entries for f in e.files] == ["move", "duplicate"]


def test_missing_source_and_occupied_wlink(tmp_path):
    fs, progress, hasher, warehouse, _, _, _ = setup(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    category = warehouse / "category"
    category.mkdir()
    target = Target("comfyui", "test", app, "models", "category", source, category)
    planner = StoragePlanner(fs, hasher, progress)
    assert planner.build([target], "replace", warehouse).entries[0].action == "missing_source"
    source.mkdir()
    (source / "WLink").mkdir()
    assert planner.build([target], "nested", warehouse).blocked


def test_wrong_warehouse_wlink_is_repaired(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard = setup(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    source.mkdir()
    category = warehouse / "category"
    category.mkdir()
    (source / "WLink").symlink_to(warehouse / "OldCategory", target_is_directory=True)
    target = Target("comfyui", "test", app, "models", "category", source, category)
    plan = StoragePlanner(fs, hasher, progress).build([target], "nested", warehouse)
    assert plan.entries[0].action == "repair_nested"
    migration_service(fs, hasher, journal, progress, guard).apply(plan)
    assert fs.same_target(source / "WLink", category)


class FailLinkFs(LocalFileSystem):
    def __init__(self):
        self.failed = False

    def symlink(self, source, target):
        if not self.failed:
            self.failed = True
            raise OSError("injected link failure")
        super().symlink(source, target)


def test_interrupted_new_migration_resumes_after_stage_cleanup(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard = setup(tmp_path, FailLinkFs())
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    source.mkdir()
    (source / "local.bin").write_bytes(b"local")
    category = warehouse / "category"
    category.mkdir()
    target = Target("comfyui", "test", app, "models", "category", source, category)
    service = migration_service(fs, hasher, journal, progress, guard)
    with pytest.raises(OSError, match="injected"):
        service.apply(StoragePlanner(fs, hasher, progress).build([target], "replace", warehouse))
    assert not source.exists()
    assert journal.load("migration-1")["status"] == "applying"
    service.resume(journal.load("migration-1"))
    service.resume(journal.load("migration-1"))
    assert source.is_symlink()
    assert journal.load("migration-1")["status"] == "applied"


def test_changed_snapshot_blocks_rollback(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard, source, _, category = legacy_fixture(tmp_path)
    service = ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]])
    service.apply_many(["legacy-1"])
    (category / "moved.bin").write_bytes(b"changed in place")
    with pytest.raises(StorageError, match="changed"):
        RecoveryService(fs, hasher, journal, progress, guard).rollback(journal.load("legacy-1"))
    assert source.is_symlink()


class NoSpaceFs(LocalFileSystem):
    def free_bytes(self, path):
        return 0


def test_insufficient_space_blocks_rollback(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard, source, _, _ = legacy_fixture(tmp_path, NoSpaceFs())
    ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]]).apply_many(["legacy-1"])
    with pytest.raises(StorageError, match="Insufficient free space"):
        RecoveryService(fs, hasher, journal, progress, guard).rollback(journal.load("legacy-1"))
    assert source.is_symlink()


class FailRestoreRenameFs(LocalFileSystem):
    def __init__(self):
        self.failed = False

    def rename(self, source, target):
        if "restore" in source.parts and not self.failed:
            self.failed = True
            raise OSError("injected restore failure")
        super().rename(source, target)


def test_interrupted_rollback_resumes(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard, source, _, _ = legacy_fixture(tmp_path, FailRestoreRenameFs())
    ConsolidationService(fs, hasher, journal, progress, guard, warehouse, [source.parents[1]]).apply_many(["legacy-1"])
    recovery = RecoveryService(fs, hasher, journal, progress, guard)
    with pytest.raises(OSError, match="injected restore failure"):
        recovery.rollback(journal.load("legacy-1"))
    assert journal.load("legacy-1")["status"] == "rolling_back"
    recovery.rollback(journal.load("legacy-1"))
    assert journal.load("legacy-1")["status"] == "rolled_back"
    assert (source / "same.bin").read_bytes() == b"same content"


def test_nested_mode_preserves_local_files(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard = setup(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    source.mkdir()
    (source / "local.bin").write_bytes(b"local")
    category = warehouse / "category"
    category.mkdir()
    target = Target("comfyui", "test", app, "models", "category", source, category)
    plan = StoragePlanner(fs, hasher, progress).build([target], "nested", warehouse)
    migration_service(fs, hasher, journal, progress, guard).apply(plan)
    assert (source / "local.bin").read_bytes() == b"local"
    assert (source / "WLink").is_symlink()
    assert StoragePlanner(fs, hasher, progress).build([target], "nested", warehouse).entries[0].action == "noop"


def test_switch_from_replace_to_nested_reconstructs_local_files(tmp_path):
    fs, progress, hasher, warehouse, _, journal, guard = setup(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    source = app / "models"
    source.mkdir()
    (source / "local.bin").write_bytes(b"local")
    category = warehouse / "category"
    category.mkdir()
    target = Target("comfyui", "test", app, "models", "category", source, category)
    planner = StoragePlanner(fs, hasher, progress)
    service = migration_service(fs, hasher, journal, progress, guard)
    migration_id = service.apply(planner.build([target], "replace", warehouse))
    assert planner.build([target], "nested", warehouse).entries[0].action == "switch_nested"
    service.apply(planner.build([target], "nested", warehouse))
    assert source.is_dir() and not source.is_symlink()
    assert (source / "local.bin").read_bytes() == b"local"
    assert (source / "WLink").resolve() == category.resolve()
    assert journal.load(migration_id)["status"] == "rolled_back"


def test_redirected_progress_is_summary(capsys):
    sink = RichProgressSink()
    sink.stage("Hashing", 4)
    sink.advance(4)
    sink.done()
    assert "Hashing: 4 / 4 bytes" in capsys.readouterr().err


def test_process_guard_detects_running_comfyui(tmp_path):
    root = tmp_path / "v018" / "ComfyUI"
    root.mkdir(parents=True)
    running = SimpleNamespace(info={"pid": 42, "name": "python.exe", "cwd": str(root.parent), "cmdline": ["python", "ComfyUI/main.py"]})
    guard = PsutilProcessGuard(lambda _: [running], lambda: 1)
    with pytest.raises(StorageError, match="running"):
        guard.assert_stopped([root])
