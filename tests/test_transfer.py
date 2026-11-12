"""Portable snapshots retain history but grant no authority in a new workspace."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings
import zipfile

from ase.projects import ProjectRegistry
from ase.state import read_record, write_record
from ase.store import Store, now
from ase.tasks import TaskService
from test_task_contract_v2 import contract

if importlib.util.find_spec("ase.transfer"):
    from ase import transfer
else:
    transfer = None


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transfer, "project transfer implementation is required")
        temporary = tempfile.TemporaryDirectory(prefix="ase-transfer-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        self.home = self.base / "state"
        with closing(ProjectRegistry(self.home)) as registry:
            self.project = registry.register(self.root, kind="document", timezone="Europe/Bucharest")
        self.store = Store(self.project)
        self.addCleanup(self.store.close)
        self.archive = self.base / "snapshot.zip"

    def seed(self):
        tasks = TaskService(self.store)
        task = tasks.create(contract("portable"))
        task = tasks.propose("portable", {"summary": "Draft the guide", "stage_ids": ["draft"]}, expected_revision=task["revision"])
        tasks.approve("portable", proposal_hash=task["proposal_hash"], user_message_ref="chat:approved", approved_stage_ids=["draft"])
        receipt = {"artifact_root": str(self.store.root / "tasks" / "portable" / "runs" / "old"), "status": "COMPLETE"}
        with self.store.db:
            write_record(self.store.db, "verification", "portable:ac", receipt, expected_revision=0)
            write_record(self.store.db, "operation", "external", {"state": "PREPARED", "task_id": "portable", "action_id": "external"}, expected_revision=0)
            self.store.db.execute("INSERT INTO knowledge_claims(topic,statement,observed_at,source_refs,created) VALUES(?,?,?,?,?)", ("policy", "Review local requirements", now(), "[]", now()))
            write_record(self.store.db, "knowledge_validity", "1", {"validity": "CURRENT"}, expected_revision=0)
            self.store.db.execute("INSERT INTO sessions(task_id,started,summary) VALUES(?,?,?)", ("portable", now(), "Retain this summary"))
        folder = self.store.root / "tasks" / "portable"
        folder.mkdir(parents=True)
        (folder / "check.json").write_text(json.dumps(receipt), encoding="utf-8")
        (folder / "command.log").write_text("Executed 3 checks", encoding="utf-8")
        for name in ("ac.session.log", "stdout.jsonl", "stdin.txt", "ac.raw.json", "request.json", "auth.json"):
            (folder / name).write_text("raw conversation must not travel", encoding="utf-8")
        (self.store.root / "publishing.json").write_text('{"enabled":true}', encoding="utf-8")
        return receipt

    def target(self, name="target"):
        result = self.base / name
        result.mkdir()
        return result

    def test_export_is_consistent_during_wal_writes(self):
        self.seed()
        with self.store.db:
            self.store.db.execute("CREATE TABLE snapshot_pairs(id INTEGER PRIMARY KEY,left_value INTEGER,right_value INTEGER)")
            self.store.db.execute("INSERT INTO snapshot_pairs VALUES(1,0,0)")
        stopped = threading.Event()
        started = threading.Event()
        failures = []
        def writer():
            try:
                with closing(sqlite3.connect(self.store.root / "memory.sqlite3")) as db:
                    started.set()
                    for value in range(1, 10000):
                        if stopped.is_set():
                            break
                        with db:
                            db.execute("UPDATE snapshot_pairs SET left_value=?,right_value=?", (value, value))
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=writer)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(stopped.set)
        self.assertTrue(started.wait(3))
        try:
            manifest = transfer.export_project(self.project, self.archive)
        finally:
            stopped.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        with zipfile.ZipFile(self.archive) as archive:
            persisted = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["project"], persisted["project"])
            for item in persisted["files"]:
                data = archive.read(item["path"])
                self.assertEqual(len(data), item["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
            snapshot = self.base / "read-snapshot.sqlite3"
            snapshot.write_bytes(archive.read("memory.sqlite3"))
        with closing(sqlite3.connect(snapshot)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            left, right = db.execute("SELECT left_value,right_value FROM snapshot_pairs").fetchone()
            self.assertEqual(left, right)
            operation = json.loads(db.execute("SELECT payload FROM runtime_records WHERE kind='operation'").fetchone()[0])
            self.assertEqual(operation["state"], "UNKNOWN")

    def test_export_excludes_auth_raw_and_verbose_by_default(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            names = archive.namelist()
            self.assertIn("tasks/portable/check.json", names)
            self.assertFalse(any(name.endswith((".log", ".jsonl", ".raw.json", "auth.json", "publishing.json", "stdin.txt", "request.json")) for name in names))
        verbose = self.base / "verbose.zip"
        transfer.export_project(self.project, verbose, include_verbose=True)
        with zipfile.ZipFile(verbose) as archive:
            self.assertIn("tasks/portable/command.log", archive.namelist())
            self.assertNotIn("tasks/portable/ac.session.log", archive.namelist())

    def test_copy_does_not_revive_approval(self):
        receipt = self.seed()
        transfer.export_project(self.project, self.archive)
        imported = transfer.import_project(self.archive, self.home, root=self.target())
        self.assertNotEqual(imported.project_id, self.project.project_id)
        self.assertNotEqual(imported.workspace_id, self.project.workspace_id)
        with closing(Store(imported)) as store:
            origin = read_record(store.db, "import_origin", "origin")["payload"]
            self.assertEqual(origin["origin_project_id"], self.project.project_id)
            self.assertEqual(read_record(store.db, "historical_verification", "portable:ac")["payload"], receipt)
            self.assertIsNone(read_record(store.db, "approval", "portable")["payload"])
            task = TaskService(store).snapshot("portable")
            self.assertEqual(task["state"], "AWAITING_APPROVAL")
            self.assertEqual(task["approved_stage_ids"], [])
            self.assertIsNone(task["proposal_hash"])
            with self.assertRaisesRegex(ValueError, "approval"):
                TaskService(store).require_approval("portable", "draft")
            with self.assertRaisesRegex(ValueError, "hash"):
                TaskService(store).approve("portable", proposal_hash="old", user_message_ref="old", approved_stage_ids=["draft"])
            self.assertEqual(read_record(store.db, "operation", "external")["payload"]["state"], "UNKNOWN")
            self.assertEqual(read_record(store.db, "knowledge_validity", "1")["payload"]["validity"], "UNKNOWN")
            self.assertEqual(store.db.execute("SELECT summary FROM sessions").fetchone()[0], "Retain this summary")
            self.assertFalse((store.root / "tasks").exists())
            historical = store.root / "historical" / "tasks" / "portable" / "check.json"
            self.assertEqual(json.loads(historical.read_text()), receipt)

    def test_restore_retains_uuid_only_without_collision(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        with self.assertRaisesRegex(ValueError, "CONFLICT"):
            transfer.import_project(self.archive, self.home, root=self.target("collision"), mode="restore")
        restored = transfer.import_project(self.archive, self.base / "restored-state", root=self.target("restored"), mode="restore")
        self.assertEqual(restored.project_id, self.project.project_id)
        self.assertNotEqual(restored.workspace_id, self.project.workspace_id)
        with closing(Store(restored)) as store:
            self.assertEqual(store.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)

    def rewrite(self, mutate):
        with zipfile.ZipFile(self.archive) as source:
            entries = [(entry, source.read(entry)) for entry in source.infolist()]
        changed = self.base / "changed.zip"
        with zipfile.ZipFile(changed, "w") as target:
            mutate(entries)
            for entry, data in entries:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    target.writestr(entry, data)
        return changed

    def assert_unregistered(self, root, home=None):
        with closing(ProjectRegistry(home or self.home)) as registry:
            with self.assertRaisesRegex(ValueError, "Unknown"):
                registry.resolve(str(root))

    def test_import_rejects_escape_and_divergent_restore(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        for name in ("../escaped", "/absolute", "C:/outside", "tasks/../escape", "tasks\\escape", "tasks/CON/file", "tasks/portable/check.json:stream"):
            with self.subTest(name=name):
                def inject(entries):
                    entries.append((zipfile.ZipInfo(name), b"escape"))
                root = self.target("target-" + str(len(list(self.base.glob("target-*")))))
                with self.assertRaises(ValueError):
                    transfer.import_project(self.rewrite(inject), self.home, root=root)
                self.assert_unregistered(root)
        self.assertFalse((self.base / "escaped").exists())
        before = (self.store.root / "memory.sqlite3").read_bytes()
        with self.assertRaisesRegex(ValueError, "CONFLICT"):
            transfer.import_project(self.archive, self.home, root=self.root)
        self.assertEqual((self.store.root / "memory.sqlite3").read_bytes(), before)

    def test_import_rejects_hash_tamper_links_and_duplicate_members(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        def tamper(entries):
            entries[:] = [(entry, b"tampered" if entry.filename.endswith("check.json") else data) for entry, data in entries]
        def symlink(entries):
            link = zipfile.ZipInfo("tasks/portable/link.json")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            entries.append((link, b"/outside"))
        def hardlink(entries):
            link = zipfile.ZipInfo("tasks/portable/link.json")
            link.create_system = 3
            link.external_attr = (stat.S_IFREG | 0o600) << 16
            link.extra = b"\x0d\x00\x08\x00outside!"
            entries.append((link, b""))
        def duplicate(entries):
            entries.append(entries[0])
        for mutation in (tamper, symlink, hardlink, duplicate):
            with self.subTest(mutation=mutation.__name__):
                root = self.target(mutation.__name__)
                with self.assertRaises(ValueError):
                    transfer.import_project(self.rewrite(mutation), self.home, root=root)
                self.assert_unregistered(root)

    def test_activation_failure_rolls_back_only_new_managed_state(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        target = self.target()
        marker = target / "existing-user-file.txt"
        marker.write_text("keep", encoding="utf-8")
        original = set((self.home / "projects").iterdir())
        with patch.object(transfer, "_activate_import", side_effect=RuntimeError("activation interrupted")):
            with self.assertRaisesRegex(RuntimeError, "activation interrupted"):
                transfer.import_project(self.archive, self.home, root=target)
        self.assert_unregistered(target)
        self.assertEqual(set((self.home / "projects").iterdir()), original)
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        imported = transfer.import_project(self.archive, self.home, root=target)
        with closing(Store(imported)) as store:
            self.assertEqual(store.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)

    def test_export_rejects_existing_destination_and_linked_artifacts(self):
        self.seed()
        self.archive.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "CONFLICT"):
            transfer.export_project(self.project, self.archive)
        self.assertEqual(self.archive.read_bytes(), b"keep")
        self.archive.unlink()
        path = self.store.root / "tasks" / "portable" / "check.json"
        os.link(path, self.base / "hardlinked.json")
        with self.assertRaises(ValueError):
            transfer.export_project(self.project, self.archive)
        self.assertFalse(self.archive.exists())

    def test_export_rejects_credentials_without_rewriting_history(self):
        self.seed()
        with self.store.db:
            self.store.db.execute("UPDATE sessions SET summary=?", ("password=private-value-must-stay-local",))
        with self.assertRaisesRegex(ValueError, "credential"):
            transfer.export_project(self.project, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.store.db.execute("SELECT summary FROM sessions").fetchone()[0], "password=private-value-must-stay-local")

    def test_import_rejects_excess_bounds_and_database_identity_tamper(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        target = self.target()
        with patch.object(transfer, "MAX_TOTAL_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "bound"):
                transfer.import_project(self.archive, self.home, root=target)
        def tamper_identity(entries):
            manifest = json.loads(next(data for entry, data in entries if entry.filename == "manifest.json"))
            manifest["project"]["project_id"] = "00000000-0000-4000-8000-000000000001"
            entries[:] = [(entry, json.dumps(manifest).encode() if entry.filename == "manifest.json" else data) for entry, data in entries]
        with self.assertRaisesRegex(ValueError, "identity"):
            transfer.import_project(self.rewrite(tamper_identity), self.home, root=target)
        self.assert_unregistered(target)

    def test_export_snapshot_preserves_original_unresolved_operation_state(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            database = self.base / "operation-snapshot.sqlite3"
            database.write_bytes(archive.read("memory.sqlite3"))
        with closing(sqlite3.connect(database)) as db:
            historical = read_record(db, "historical_operation", "external")["payload"]
            self.assertIsNotNone(historical)
            self.assertEqual(historical["state"], "PREPARED")
        self.assertEqual(read_record(self.store.db, "operation", "external")["payload"]["state"], "PREPARED")

    def test_invalid_archive_does_not_create_state_home(self):
        invalid = self.base / "invalid.zip"
        invalid.write_bytes(b"not a zip")
        home = self.base / "unused-state"
        with self.assertRaises(ValueError):
            transfer.import_project(invalid, home, root=self.target())
        self.assertFalse(home.exists())

    def test_export_cannot_capture_its_own_state_destination(self):
        self.seed()
        with self.assertRaisesRegex(ValueError, "project state"):
            transfer.export_project(self.project, self.store.root / "nested.zip")

    def test_copy_can_be_exported_again_without_losing_older_artifacts(self):
        receipt = self.seed()
        transfer.export_project(self.project, self.archive)
        first = transfer.import_project(self.archive, self.home, root=self.target("first-copy"))
        second_archive = self.base / "second.zip"
        transfer.export_project(first, second_archive)
        with zipfile.ZipFile(second_archive) as archive:
            self.assertIn("historical/tasks/portable/check.json", archive.namelist())
            self.assertIn("historical/memory.sqlite3", archive.namelist())
        second = transfer.import_project(second_archive, self.home, root=self.target("second-copy"))
        with closing(Store(second)) as store:
            self.assertEqual(store.db.execute("SELECT summary FROM sessions").fetchone()[0], "Retain this summary")
            self.assertEqual(json.loads((store.root / "historical" / "historical" / "tasks" / "portable" / "check.json").read_text()), receipt)
            with self.assertRaisesRegex(ValueError, "approval"):
                TaskService(store).require_approval("portable", "draft")

    def test_process_crash_preserves_history_and_retry_identifies_orphan(self):
        self.seed()
        transfer.export_project(self.project, self.archive)
        target = self.target()
        program = ("import os\nfrom pathlib import Path\nfrom ase import transfer\n"
                   "transfer._activate_import=lambda *args: os._exit(86)\n"
                   f"transfer.import_project(Path({str(self.archive)!r}),Path({str(self.home)!r}),root=Path({str(target)!r}))\n")
        crashed = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=15)
        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        self.assert_unregistered(target)
        orphans = [folder for folder in (self.home / "projects").iterdir() if folder.name != self.project.storage_key and not folder.name.startswith(".import-")]
        self.assertEqual(len(orphans), 1)
        with closing(sqlite3.connect(orphans[0] / "memory.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT summary FROM sessions").fetchone()[0], "Retain this summary")
        with self.assertRaisesRegex(ValueError, "CONFLICT") as rejected:
            transfer.import_project(self.archive, self.home, root=target)
        self.assertIn(str(orphans[0]), str(rejected.exception))
        self.assertTrue((orphans[0] / "historical" / "memory.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
