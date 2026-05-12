from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "codex-delete-conversation" / "scripts" / "codex_conversation_delete.py"
SKILL = ROOT / "skills" / "codex-delete-conversation" / "SKILL.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CodexHomeFixture:
    def __init__(self, root: Path):
        self.root = root
        self.codex_home = root / ".codex"
        self.db = self.codex_home / "state_5.sqlite"
        self.index = self.codex_home / "session_index.jsonl"
        self.sessions = self.codex_home / "sessions"

    def build(self) -> "CodexHomeFixture":
        self.codex_home.mkdir()
        self.sessions.mkdir()
        self._create_database()
        self._create_rollouts()
        self._create_index()
        return self

    def _create_database(self) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                create table threads (
                    id text primary key,
                    title text not null,
                    rollout_path text not null,
                    archived integer not null default 0,
                    updated_at_ms integer
                );
                create table thread_dynamic_tools (
                    thread_id text not null references threads(id) on delete cascade,
                    position integer not null,
                    name text not null,
                    description text not null,
                    input_schema text not null,
                    primary key(thread_id, position)
                );
                create table stage1_outputs (
                    thread_id text primary key references threads(id) on delete cascade,
                    raw_memory text not null
                );
                create table thread_goals (
                    thread_id text primary key references threads(id) on delete cascade,
                    goal_id text not null
                );
                create table thread_spawn_edges (
                    parent_thread_id text not null,
                    child_thread_id text not null primary key,
                    status text not null
                );
                create table agent_job_items (
                    job_id text not null,
                    item_id text not null,
                    assigned_thread_id text,
                    primary key(job_id, item_id)
                );
                """
            )
            rows = [
                ("thread-one", "Delete me\n第二行", str(self.sessions / "rollout-thread-one.jsonl"), 0, 2000),
                ("thread-two", "Delete me", str(self.sessions / "rollout-thread-two.jsonl"), 0, 1000),
            ]
            conn.executemany("insert into threads values (?,?,?,?,?)", rows)
            conn.execute(
                "insert into thread_dynamic_tools values (?,?,?,?,?)",
                ("thread-one", 0, "tool", "desc", "{}"),
            )
            conn.execute("insert into stage1_outputs values (?,?)", ("thread-one", "memory"))
            conn.execute("insert into thread_goals values (?,?)", ("thread-one", "goal"))
            conn.execute("insert into thread_spawn_edges values (?,?,?)", ("thread-one", "child-one", "done"))
            conn.execute("insert into agent_job_items values (?,?,?)", ("job", "item", "thread-one"))

    def _create_rollouts(self) -> None:
        (self.sessions / "rollout-thread-one.jsonl").write_text('{"id":"thread-one"}\n', encoding="utf-8")
        (self.sessions / "rollout-thread-two.jsonl").write_text('{"id":"thread-two"}\n', encoding="utf-8")

    def _create_index(self) -> None:
        self.index.write_text(
            "\n".join(
                [
                    json.dumps({"id": "thread-one", "thread_name": "Delete me\n第二行", "updated_at": "2026-01-01T00:00:00Z"}),
                    json.dumps({"id": "thread-two", "thread_name": "Delete me", "updated_at": "2026-01-02T00:00:00Z"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = CodexHomeFixture(Path(self.tmp.name)).build()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(SCRIPT), "--json", "--codex-home", str(self.fixture.codex_home), *args]
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        result = subprocess.run(cmd, text=True, capture_output=True, env=merged_env)
        if check and result.returncode != 0:
            raise AssertionError(f"command failed: {cmd}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def run_human_cli(self, *args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(SCRIPT), "--codex-home", str(self.fixture.codex_home), *args]
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        result = subprocess.run(cmd, text=True, capture_output=True, env=merged_env)
        if check and result.returncode != 0:
            raise AssertionError(f"command failed: {cmd}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def json_stdout(self, *args: str, **kwargs) -> dict:
        return json.loads(self.run_cli(*args, **kwargs).stdout)

    def test_find_by_title_and_id(self) -> None:
        by_title = self.json_stdout("find", "--title", "Delete me")
        self.assertEqual(by_title["count"], 2)
        by_id = self.json_stdout("find", "--id", "thread-one")
        self.assertEqual(by_id["threads"][0]["id"], "thread-one")

    def test_human_output_compacts_multiline_titles(self) -> None:
        result = self.run_human_cli("find", "--id", "thread-one")

        self.assertIn("Delete me 第二行", result.stdout)
        self.assertEqual(len([line for line in result.stdout.splitlines() if line.strip()]), 1)

    def test_dry_run_changes_nothing(self) -> None:
        db_hash = sha256(self.fixture.db)
        index_hash = sha256(self.fixture.index)
        rollout = self.fixture.sessions / "rollout-thread-one.jsonl"
        rollout_hash = sha256(rollout)

        data = self.json_stdout("delete", "--id", "thread-one", "--dry-run")

        self.assertFalse(data["changed_anything"])
        self.assertEqual(db_hash, sha256(self.fixture.db))
        self.assertEqual(index_hash, sha256(self.fixture.index))
        self.assertEqual(rollout_hash, sha256(rollout))
        self.assertFalse((self.fixture.codex_home / "deleted_conversation_backups").exists())

    def test_real_delete_requires_matching_confirmation(self) -> None:
        result = self.run_cli("delete", "--id", "thread-one", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --confirm", result.stderr)
        with sqlite3.connect(self.fixture.db) as conn:
            count = conn.execute("select count(*) from threads where id='thread-one'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_delete_creates_backup_and_removes_thread(self) -> None:
        data = self.json_stdout("delete", "--id", "thread-one", "--confirm", "thread-one")

        self.assertTrue(data["changed_anything"])
        self.assertEqual(data["remaining_db_matches"], 0)
        self.assertEqual(data["remaining_index_matches"], 0)
        self.assertFalse(data["rollout_exists"])
        backup = Path(data["backup_dir"])
        self.assertTrue((backup / "state_5.sqlite").exists())
        self.assertTrue((backup / "session_index.jsonl").exists())
        self.assertTrue((backup / "rollout.jsonl").exists())
        self.assertTrue((backup / "manifest.json").exists())
        self.assertEqual(data["backed_up_index_lines"], 1)
        self.assertNotIn("thread-one", self.fixture.index.read_text(encoding="utf-8"))
        with sqlite3.connect(self.fixture.db) as conn:
            assigned = conn.execute("select assigned_thread_id from agent_job_items").fetchone()[0]
        self.assertIsNone(assigned)

    def test_restore_rehydrates_deleted_thread_without_replacing_other_threads(self) -> None:
        deleted = self.json_stdout("delete", "--id", "thread-one", "--confirm", "thread-one")
        restored = self.json_stdout("restore", "--backup-dir", deleted["backup_dir"])

        self.assertTrue(restored["rollout_exists"])
        self.assertTrue((self.fixture.sessions / "rollout-thread-one.jsonl").exists())
        with sqlite3.connect(self.fixture.db) as conn:
            ids = {row[0] for row in conn.execute("select id from threads")}
            dyn_count = conn.execute("select count(*) from thread_dynamic_tools where thread_id='thread-one'").fetchone()[0]
        self.assertEqual(ids, {"thread-one", "thread-two"})
        self.assertEqual(dyn_count, 1)
        self.assertIn("thread-one", self.fixture.index.read_text(encoding="utf-8"))

    def test_restore_existing_thread_fails_safely(self) -> None:
        deleted = self.json_stdout("delete", "--id", "thread-one", "--confirm", "thread-one")
        self.json_stdout("restore", "--backup-dir", deleted["backup_dir"])

        result = self.run_cli("restore", "--backup-dir", deleted["backup_dir"], check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("thread already exists", result.stderr)

    def test_restore_synthesizes_missing_index_line(self) -> None:
        self.fixture.index.write_text(
            json.dumps({"id": "thread-two", "thread_name": "Delete me", "updated_at": "2026-01-02T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        deleted = self.json_stdout("delete", "--id", "thread-one", "--confirm", "thread-one")
        self.assertEqual(deleted["backed_up_index_lines"], 0)
        self.assertTrue(deleted["warnings"])

        self.json_stdout("restore", "--backup-dir", deleted["backup_dir"])

        index_text = self.fixture.index.read_text(encoding="utf-8")
        self.assertIn("thread-one", index_text)
        self.assertIn("Delete me", index_text)

    def test_restore_to_different_codex_home_remaps_rollout_path(self) -> None:
        deleted = self.json_stdout("delete", "--id", "thread-one", "--confirm", "thread-one")
        target_root = Path(self.tmp.name) / "target"
        target_codex = target_root / ".codex"
        target_codex.mkdir(parents=True)
        (target_codex / "sessions").mkdir()
        shutil.copy2(self.fixture.db, target_codex / "state_5.sqlite")
        shutil.copy2(self.fixture.index, target_codex / "session_index.jsonl")

        cmd = [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--codex-home",
            str(target_codex),
            "restore",
            "--backup-dir",
            deleted["backup_dir"],
        ]
        result = subprocess.run(cmd, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = json.loads(result.stdout)

        expected_rollout = target_codex / "sessions" / "rollout-thread-one.jsonl"
        self.assertEqual(Path(restored["rollout_path"]).resolve(), expected_rollout.resolve())
        self.assertTrue(expected_rollout.exists())
        with sqlite3.connect(target_codex / "state_5.sqlite") as conn:
            rollout_path = conn.execute("select rollout_path from threads where id='thread-one'").fetchone()[0]
        self.assertEqual(Path(rollout_path).resolve(), expected_rollout.resolve())

    def test_missing_rollout_prevents_delete_and_backup(self) -> None:
        (self.fixture.sessions / "rollout-thread-one.jsonl").unlink()

        result = self.run_cli("delete", "--id", "thread-one", "--confirm", "thread-one", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rollout file does not exist", result.stderr)
        self.assertFalse((self.fixture.codex_home / "deleted_conversation_backups").exists())
        with sqlite3.connect(self.fixture.db) as conn:
            count = conn.execute("select count(*) from threads where id='thread-one'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_current_uses_codex_thread_id(self) -> None:
        data = self.json_stdout("delete", "--current", "--dry-run", env={"CODEX_THREAD_ID": "thread-one"})

        self.assertEqual(data["thread"]["id"], "thread-one")
        self.assertFalse(data["changed_anything"])

    def test_doctor(self) -> None:
        data = self.json_stdout("doctor")

        self.assertTrue(data["schema_ok"])
        self.assertEqual(data["thread_count"], 2)

    def test_chinese_language_flag_for_human_output(self) -> None:
        result = self.run_human_cli("--lang", "zh-CN", "delete", "--id", "thread-one", "--dry-run")

        self.assertIn("仅模拟执行", result.stdout)
        self.assertIn("changed_anything=false", result.stdout)

    def test_chinese_locale_auto_for_human_output(self) -> None:
        result = self.run_human_cli("list-backups", env={"LC_ALL": "zh_CN.UTF-8"})

        self.assertIn("没有找到删除备份", result.stdout)

    def test_chinese_error_output(self) -> None:
        result = self.run_human_cli("--lang", "zh-CN", "delete", "--id", "thread-one", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("错误", result.stderr)
        self.assertIn("真实删除", result.stderr)

    def test_skill_frontmatter_is_valid(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---", 2)[1]
        self.assertIn("name: codex-delete-conversation", frontmatter)
        self.assertIn("description:", frontmatter)


if __name__ == "__main__":
    unittest.main()
