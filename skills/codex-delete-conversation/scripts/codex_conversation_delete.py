#!/usr/bin/env python3
"""Safely find, delete, and restore local Codex conversation threads."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CODEX_HOME = Path.home() / ".codex"
DB_NAME = "state_5.sqlite"
INDEX_NAME = "session_index.jsonl"
BACKUP_DIR_NAME = "deleted_conversation_backups"
REQUIRED_THREAD_COLUMNS = {"id", "title", "rollout_path", "archived", "updated_at_ms"}
THREAD_RELATED_TABLES = ("thread_dynamic_tools", "stage1_outputs", "thread_goals")
LANG_EN = "en"
LANG_ZH = "zh-CN"

MESSAGES = {
    LANG_EN: {
        "backup_manifest_missing": "Backup manifest does not exist: {path}",
        "backup_db_missing": "Backup database does not exist: {path}",
        "backup_rollout_missing": "Backup rollout does not exist: {path}",
        "backup_thread_row_bad": "Backup did not contain exactly one threads row for: {thread_id}",
        "codex_home_missing": "Codex home does not exist: {path}",
        "current_missing": "CODEX_THREAD_ID is not set; cannot resolve --current",
        "db_missing": "Codex database does not exist: {path}",
        "delete_done": "Deleted thread: {thread_id}\nBackup directory: {backup_dir}\nremaining_db_matches={db} remaining_index_matches={index} rollout_exists={rollout}",
        "doctor": "Codex delete conversation doctor: {status}\nCodex home: {codex_home}",
        "dry_run": "Dry run only. Would delete:\n{thread}\nWould create backup: {backup_dir}\nchanged_anything=false",
        "error_prefix": "Error",
        "index_missing": "Codex session index does not exist: {path}",
        "missing_table": "Codex database is missing required table: threads",
        "missing_thread_columns": "threads table is missing required columns: {columns}",
        "no_backups": "No deletion backups found.",
        "no_matching_threads": "No matching threads found.",
        "no_thread": "No thread found for id: {thread_id}",
        "optional_table_missing": "Optional table not found: {table}",
        "restore_done": "Restored thread: {thread_id}\nPre-restore backup: {backup_dir}\nrollout_exists={rollout}",
        "restore_existing_db": "Refusing to restore: thread already exists in database: {thread_id}",
        "restore_existing_rollout": "Refusing to restore: rollout path already exists: {path}",
        "rollout_missing": "Refusing to delete: rollout file does not exist: {path}",
        "rollout_not_file": "Refusing to delete: rollout path is not a file: {path}",
        "target_both": "Use either --current or --id, not both",
        "target_required": "Provide --id THREAD_ID or --current",
        "thread_not_unique": "Thread id is not unique, refusing to continue: {thread_id}",
        "unexpected_prefix": "Unexpected error",
        "warnings": "Warnings:",
        "confirm_required": "Real deletion requires --confirm <thread-id> matching the target id",
        "cross_home_rollout_unmapped": "Backup rollout path is outside the original Codex home; refusing cross-home restore: {path}",
    },
    LANG_ZH: {
        "backup_manifest_missing": "找不到备份清单：{path}",
        "backup_db_missing": "找不到备份数据库：{path}",
        "backup_rollout_missing": "找不到备份正文文件：{path}",
        "backup_thread_row_bad": "备份中没有且仅有一条对应的 threads 记录：{thread_id}",
        "codex_home_missing": "找不到 Codex 数据目录：{path}",
        "current_missing": "未设置 CODEX_THREAD_ID，无法解析 --current",
        "db_missing": "找不到 Codex 数据库：{path}",
        "delete_done": "已删除会话：{thread_id}\n备份目录：{backup_dir}\nremaining_db_matches={db} remaining_index_matches={index} rollout_exists={rollout}",
        "doctor": "Codex 会话删除工具检查：{status}\nCodex 数据目录：{codex_home}",
        "dry_run": "仅模拟执行，不会修改任何内容。将删除：\n{thread}\n将创建备份：{backup_dir}\nchanged_anything=false",
        "error_prefix": "错误",
        "index_missing": "找不到 Codex 会话索引：{path}",
        "missing_table": "Codex 数据库缺少必要表：threads",
        "missing_thread_columns": "threads 表缺少必要列：{columns}",
        "no_backups": "没有找到删除备份。",
        "no_matching_threads": "没有找到匹配的会话。",
        "no_thread": "没有找到这个线程 ID：{thread_id}",
        "optional_table_missing": "未找到可选表：{table}",
        "restore_done": "已恢复会话：{thread_id}\n恢复前备份：{backup_dir}\nrollout_exists={rollout}",
        "restore_existing_db": "拒绝恢复：数据库中已存在这个线程：{thread_id}",
        "restore_existing_rollout": "拒绝恢复：正文文件路径已存在：{path}",
        "rollout_missing": "拒绝删除：找不到正文文件：{path}",
        "rollout_not_file": "拒绝删除：正文路径不是文件：{path}",
        "target_both": "只能使用 --current 或 --id 其中一个",
        "target_required": "请提供 --id THREAD_ID 或 --current",
        "thread_not_unique": "线程 ID 不唯一，拒绝继续：{thread_id}",
        "unexpected_prefix": "意外错误",
        "warnings": "警告：",
        "confirm_required": "真实删除必须提供与目标一致的 --confirm <thread-id>",
        "cross_home_rollout_unmapped": "备份正文路径不在原 Codex 数据目录内，拒绝跨目录恢复：{path}",
    },
}


class CliError(Exception):
    """User-facing command failure with optional localization metadata."""

    def __init__(self, fallback: str, key: str | None = None, **params: Any):
        super().__init__(fallback)
        self.key = key
        self.params = params


@dataclass(frozen=True)
class Paths:
    codex_home: Path
    db: Path
    index: Path
    backup_root: Path


@dataclass(frozen=True)
class Thread:
    id: str
    title: str
    rollout_path: str
    archived: int
    updated_at_ms: int | None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def detect_language(args: argparse.Namespace | None = None) -> str:
    requested = getattr(args, "lang", "auto") if args is not None else "auto"
    if requested in (LANG_EN, LANG_ZH):
        return requested
    locale = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
        or ""
    ).lower()
    return LANG_ZH if locale.startswith("zh") else LANG_EN


def msg(lang: str, key: str, **params: Any) -> str:
    template = MESSAGES.get(lang, MESSAGES[LANG_EN]).get(key, MESSAGES[LANG_EN][key])
    return template.format(**params)


def localized_error(error: CliError, lang: str) -> str:
    if error.key:
        return msg(lang, error.key, **error.params)
    return str(error)


def cli_error(key: str, **params: Any) -> CliError:
    return CliError(msg(LANG_EN, key, **params), key, **params)


def resolve_paths(args: argparse.Namespace | None = None) -> Paths:
    override = getattr(args, "codex_home", None) if args is not None else None
    raw_home = override or os.environ.get("CODEX_HOME") or DEFAULT_CODEX_HOME
    codex_home = Path(raw_home).expanduser().resolve()
    return Paths(
        codex_home=codex_home,
        db=codex_home / DB_NAME,
        index=codex_home / INDEX_NAME,
        backup_root=codex_home / BACKUP_DIR_NAME,
    )


def connect(paths: Paths, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = paths.db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    if not table_exists(conn, name):
        return []
    return [row["name"] for row in conn.execute(f"pragma table_info({quote_ident(name)})")]


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def require_environment(paths: Paths) -> None:
    if not paths.codex_home.exists():
        raise cli_error("codex_home_missing", path=paths.codex_home)
    if not paths.db.exists():
        raise cli_error("db_missing", path=paths.db)
    if not paths.index.exists():
        raise cli_error("index_missing", path=paths.index)
    with connect(paths, readonly=True) as conn:
        if not table_exists(conn, "threads"):
            raise cli_error("missing_table")
        columns = set(table_columns(conn, "threads"))
        missing = sorted(REQUIRED_THREAD_COLUMNS - columns)
        if missing:
            raise cli_error("missing_thread_columns", columns=", ".join(missing))


def thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        id=str(row["id"]),
        title=str(row["title"]),
        rollout_path=str(row["rollout_path"]),
        archived=int(row["archived"] or 0),
        updated_at_ms=row["updated_at_ms"],
    )


def find_threads(
    paths: Paths,
    *,
    thread_id: str | None = None,
    title: str | None = None,
    exact: bool = False,
    cwd: str | None = None,
    limit: int = 20,
) -> list[Thread]:
    require_environment(paths)
    clauses: list[str] = []
    params: list[Any] = []

    if thread_id:
        clauses.append("id = ?")
        params.append(thread_id)
    if title:
        clauses.append("title = ?" if exact else "title like ?")
        params.append(title if exact else f"%{title}%")
    if cwd:
        clauses.append("cwd = ?")
        params.append(str(Path(cwd).expanduser().resolve()))

    where = " where " + " and ".join(clauses) if clauses else ""
    safe_limit = max(1, int(limit))
    query = (
        "select id,title,rollout_path,archived,updated_at_ms "
        f"from threads{where} order by updated_at_ms desc, id desc limit ?"
    )

    with connect(paths, readonly=True) as conn:
        return [thread_from_row(row) for row in conn.execute(query, [*params, safe_limit])]


def get_thread(paths: Paths, thread_id: str) -> Thread:
    matches = find_threads(paths, thread_id=thread_id, limit=2)
    if not matches:
        raise cli_error("no_thread", thread_id=thread_id)
    if len(matches) > 1:
        raise cli_error("thread_not_unique", thread_id=thread_id)
    return matches[0]


def resolve_current_thread_id() -> str:
    thread_id = os.environ.get("CODEX_THREAD_ID")
    if not thread_id:
        raise cli_error("current_missing")
    return thread_id


def resolve_target_thread_id(args: argparse.Namespace) -> str:
    if args.current and args.id:
        raise cli_error("target_both")
    if args.current:
        return resolve_current_thread_id()
    if args.id:
        return args.id
    raise cli_error("target_required")


def read_index_lines(paths: Paths) -> list[str]:
    return paths.index.read_text(encoding="utf-8").splitlines()


def index_lines_for_thread(paths: Paths, thread_id: str) -> list[str]:
    lines = []
    for line in read_index_lines(paths):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == thread_id:
            lines.append(line)
    return lines


def index_without_thread(paths: Paths, thread_id: str) -> str:
    kept = []
    for line in read_index_lines(paths):
        remove = False
        try:
            obj = json.loads(line)
            remove = obj.get("id") == thread_id
        except json.JSONDecodeError:
            remove = False
        if not remove:
            kept.append(line)
    return "\n".join(kept) + ("\n" if kept else "")


def compact_human_text(value: str, *, limit: int = 140) -> str:
    compact = " ".join(str(value).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "..."


def default_index_line(thread: Thread) -> str:
    if thread.updated_at_ms:
        updated_at = datetime.fromtimestamp(thread.updated_at_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps(
        {"id": thread.id, "thread_name": thread.title, "updated_at": updated_at},
        ensure_ascii=False,
    )


def write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{utc_timestamp()}")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def backup_sqlite(paths: Paths, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(paths.db) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def rollout_path_for(thread: Thread) -> Path:
    return Path(thread.rollout_path).expanduser()


def path_relative_to(path: Path, parent: Path) -> Path | None:
    try:
        return path.resolve().relative_to(parent.resolve())
    except ValueError:
        return None


def restore_rollout_path(paths: Paths, manifest: dict[str, Any], thread: Thread) -> Path:
    original = rollout_path_for(thread)
    raw_manifest_home = manifest.get("codex_home")
    if not raw_manifest_home:
        return original
    manifest_home = Path(raw_manifest_home).expanduser()
    relative = path_relative_to(original, manifest_home)
    if relative is None:
        if paths.codex_home.resolve() == manifest_home.resolve():
            return original
        raise cli_error("cross_home_rollout_unmapped", path=original)
    return paths.codex_home / relative


def preflight_delete(paths: Paths, thread: Thread) -> None:
    require_environment(paths)
    rollout = rollout_path_for(thread)
    if not rollout.exists():
        raise cli_error("rollout_missing", path=rollout)
    if not rollout.is_file():
        raise cli_error("rollout_not_file", path=rollout)


def make_delete_backup(paths: Paths, thread: Thread) -> Path:
    preflight_delete(paths, thread)
    backup_dir = paths.backup_root / f"{utc_timestamp()}-{thread.id}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    backup_sqlite(paths, backup_dir / DB_NAME)
    shutil.copy2(paths.index, backup_dir / INDEX_NAME)
    shutil.copy2(rollout_path_for(thread), backup_dir / "rollout.jsonl")
    index_lines = index_lines_for_thread(paths, thread.id)

    manifest = {
        "format_version": 1,
        "created_at_utc": utc_timestamp(),
        "codex_home": str(paths.codex_home),
        "thread": asdict(thread),
        "index_lines": index_lines,
        "index_line_count": len(index_lines),
        "privacy_notice": "This backup contains the full local conversation transcript.",
        "files": {
            DB_NAME: str(backup_dir / DB_NAME),
            INDEX_NAME: str(backup_dir / INDEX_NAME),
            "rollout.jsonl": str(backup_dir / "rollout.jsonl"),
        },
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_dir


def restore_files_from_backup(paths: Paths, backup_dir: Path) -> None:
    manifest = load_manifest(backup_dir)
    thread = Thread(**manifest["thread"])
    rollout = rollout_path_for(thread)
    rollout.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(backup_dir / DB_NAME, paths.db)
    remove_sqlite_sidecars(paths.db)
    shutil.copy2(backup_dir / INDEX_NAME, paths.index)
    shutil.copy2(backup_dir / "rollout.jsonl", rollout)


def remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in table_columns(conn, table)


def delete_thread_record(paths: Paths, thread: Thread) -> None:
    with connect(paths) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN")
        if table_exists(conn, "thread_spawn_edges"):
            conn.execute(
                "delete from thread_spawn_edges where parent_thread_id = ? or child_thread_id = ?",
                (thread.id, thread.id),
            )
        if table_exists(conn, "agent_job_items") and table_has_column(conn, "agent_job_items", "assigned_thread_id"):
            conn.execute(
                "update agent_job_items set assigned_thread_id = null where assigned_thread_id = ?",
                (thread.id,),
            )
        conn.execute("delete from threads where id = ?", (thread.id,))
        conn.commit()


def delete_thread(paths: Paths, thread: Thread) -> dict[str, Any]:
    backup_dir = make_delete_backup(paths, thread)
    try:
        new_index = index_without_thread(paths, thread.id)
        delete_thread_record(paths, thread)
        write_text_atomic(paths.index, new_index)
        rollout_path_for(thread).unlink()
    except Exception:
        restore_files_from_backup(paths, backup_dir)
        raise

    report = verify_deleted(paths, thread)
    report["backup_dir"] = str(backup_dir)
    manifest = load_manifest(backup_dir)
    report["backed_up_index_lines"] = manifest.get("index_line_count", len(manifest.get("index_lines") or []))
    report["warnings"] = []
    if report["backed_up_index_lines"] == 0:
        report["warnings"].append("No session_index entry existed for this thread; restore will synthesize one.")
    return report


def verify_deleted(paths: Paths, thread: Thread) -> dict[str, Any]:
    remaining_db = 0
    if paths.db.exists():
        with connect(paths, readonly=True) as conn:
            remaining_db = conn.execute(
                "select count(*) from threads where id = ?",
                (thread.id,),
            ).fetchone()[0]
    index_text = paths.index.read_text(encoding="utf-8") if paths.index.exists() else ""
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "remaining_db_matches": remaining_db,
        "remaining_index_matches": index_text.count(thread.id),
        "rollout_exists": rollout_path_for(thread).exists(),
        "changed_anything": True,
    }


def delete_plan(paths: Paths, thread: Thread) -> dict[str, Any]:
    preflight_delete(paths, thread)
    backup_dir = paths.backup_root / f"{utc_timestamp()}-{thread.id}"
    return {
        "dry_run": True,
        "thread": asdict(thread),
        "would_create_backup_dir": str(backup_dir),
        "would_backup": [
            str(paths.db),
            str(paths.index),
            thread.rollout_path,
        ],
        "would_remove_from": [
            "threads",
            "thread_spawn_edges",
            "agent_job_items.assigned_thread_id",
            str(paths.index),
        ],
        "would_unlink_rollout": thread.rollout_path,
        "changed_anything": False,
    }


def load_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise cli_error("backup_manifest_missing", path=manifest_path)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def list_backup_records(paths: Paths) -> list[dict[str, Any]]:
    if not paths.backup_root.exists():
        return []
    records = []
    for manifest_path in sorted(paths.backup_root.glob("*/manifest.json"), reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            records.append(
                {
                    "backup_dir": str(manifest_path.parent),
                    "thread_id": manifest["thread"]["id"],
                    "title": manifest["thread"]["title"],
                    "created_at_utc": manifest.get("created_at_utc"),
                }
            )
        except Exception as exc:
            records.append({"backup_dir": str(manifest_path.parent), "error": str(exc)})
    return records


def copy_table_rows_for_thread(
    conn: sqlite3.Connection,
    table: str,
    predicate: str,
    params: Iterable[Any],
) -> int:
    if not table_exists(conn, table):
        return 0
    backup_columns = [row["name"] for row in conn.execute(f"pragma backup.table_info({quote_ident(table)})")]
    current_columns = table_columns(conn, table)
    columns = [col for col in current_columns if col in backup_columns]
    if not columns:
        return 0
    quoted = ", ".join(quote_ident(col) for col in columns)
    before = conn.total_changes
    conn.execute(
        f"insert or replace into {quote_ident(table)} ({quoted}) "
        f"select {quoted} from backup.{quote_ident(table)} where {predicate}",
        tuple(params),
    )
    return conn.total_changes - before


def restore_thread(paths: Paths, backup_dir: Path) -> dict[str, Any]:
    require_environment(paths)
    manifest = load_manifest(backup_dir)
    thread = Thread(**manifest["thread"])
    rollout = restore_rollout_path(paths, manifest, thread)
    if not (backup_dir / DB_NAME).exists():
        raise cli_error("backup_db_missing", path=backup_dir / DB_NAME)
    if not (backup_dir / "rollout.jsonl").exists():
        raise cli_error("backup_rollout_missing", path=backup_dir / "rollout.jsonl")
    with connect(paths, readonly=True) as conn:
        existing = conn.execute("select count(*) from threads where id = ?", (thread.id,)).fetchone()[0]
    if existing:
        raise cli_error("restore_existing_db", thread_id=thread.id)
    if rollout.exists():
        raise cli_error("restore_existing_rollout", path=rollout)

    pre_restore_dir = paths.backup_root / f"pre-restore-{utc_timestamp()}-{thread.id}"
    pre_restore_dir.mkdir(parents=True, exist_ok=False)
    backup_sqlite(paths, pre_restore_dir / DB_NAME)
    shutil.copy2(paths.index, pre_restore_dir / INDEX_NAME)

    try:
        with connect(paths) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("attach database ? as backup", (str(backup_dir / DB_NAME),))
            conn.execute("BEGIN")
            restored_thread_rows = copy_table_rows_for_thread(conn, "threads", "id = ?", (thread.id,))
            if restored_thread_rows != 1:
                raise cli_error("backup_thread_row_bad", thread_id=thread.id)
            if str(rollout) != thread.rollout_path:
                conn.execute("update threads set rollout_path = ? where id = ?", (str(rollout), thread.id))
            for table in THREAD_RELATED_TABLES:
                copy_table_rows_for_thread(conn, table, "thread_id = ?", (thread.id,))
            copy_table_rows_for_thread(
                conn,
                "thread_spawn_edges",
                "parent_thread_id = ? or child_thread_id = ?",
                (thread.id, thread.id),
            )
            conn.commit()
            conn.execute("detach database backup")

        index_text = paths.index.read_text(encoding="utf-8")
        if thread.id not in index_text:
            index_lines = manifest.get("index_lines") or [default_index_line(thread)]
            index_text = index_text.rstrip("\n") + ("\n" if index_text.strip() else "") + "\n".join(index_lines) + "\n"
            write_text_atomic(paths.index, index_text)
        rollout.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_dir / "rollout.jsonl", rollout)
    except Exception:
        shutil.copy2(pre_restore_dir / DB_NAME, paths.db)
        remove_sqlite_sidecars(paths.db)
        shutil.copy2(pre_restore_dir / INDEX_NAME, paths.index)
        if rollout.exists():
            rollout.unlink()
        raise

    return {
        "restored_thread_id": thread.id,
        "title": thread.title,
        "rollout_path": str(rollout),
        "rollout_exists": rollout.exists(),
        "pre_restore_backup_dir": str(pre_restore_dir),
        "changed_anything": True,
    }


def doctor(paths: Paths) -> dict[str, Any]:
    checks = {
        "codex_home_exists": paths.codex_home.exists(),
        "database_exists": paths.db.exists(),
        "index_exists": paths.index.exists(),
        "backup_root": str(paths.backup_root),
        "schema_ok": False,
        "thread_count": None,
        "warnings": [],
    }
    if not paths.db.exists():
        checks["warnings"].append(f"Missing database: {paths.db}")
        return checks
    try:
        with connect(paths, readonly=True) as conn:
            columns = set(table_columns(conn, "threads"))
            missing = sorted(REQUIRED_THREAD_COLUMNS - columns)
            checks["schema_ok"] = not missing
            if missing:
                checks["warnings"].append("Missing threads columns: " + ", ".join(missing))
            if table_exists(conn, "threads"):
                checks["thread_count"] = conn.execute("select count(*) from threads").fetchone()[0]
            for table in ("thread_spawn_edges", "agent_job_items"):
                if not table_exists(conn, table):
                    checks["warnings"].append(f"Optional table not found: {table}")
    except Exception as exc:
        checks["warnings"].append(str(exc))
    return checks


def as_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def print_output(args: argparse.Namespace, data: Any, human: str) -> None:
    print(as_json(data) if args.json else human)


def format_thread(thread: Thread) -> str:
    return f"{thread.id} | {compact_human_text(thread.title)} | archived={thread.archived} | {thread.rollout_path}"


def cmd_find(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    if args.current and args.id:
        raise cli_error("target_both")
    thread_id = resolve_current_thread_id() if args.current else args.id
    threads = find_threads(
        paths,
        thread_id=thread_id,
        title=args.title,
        exact=args.exact,
        cwd=args.cwd,
        limit=args.limit,
    )
    data = {"count": len(threads), "threads": [asdict(thread) for thread in threads]}
    lang = detect_language(args)
    human = msg(lang, "no_matching_threads") if not threads else "\n".join(format_thread(thread) for thread in threads)
    print_output(args, data, human)


def cmd_delete(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    thread_id = resolve_target_thread_id(args)
    thread = get_thread(paths, thread_id)
    if args.dry_run:
        data = delete_plan(paths, thread)
        human = msg(
            detect_language(args),
            "dry_run",
            thread=format_thread(thread),
            backup_dir=data["would_create_backup_dir"],
        )
        print_output(args, data, human)
        return
    if args.confirm != thread.id:
        raise cli_error("confirm_required")
    data = delete_thread(paths, thread)
    human = msg(
        detect_language(args),
        "delete_done",
        thread_id=thread.id,
        backup_dir=data["backup_dir"],
        db=data["remaining_db_matches"],
        index=data["remaining_index_matches"],
        rollout=str(data["rollout_exists"]).lower(),
    )
    print_output(args, data, human)


def cmd_restore(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    backup_dir = Path(args.backup_dir).expanduser().resolve()
    data = restore_thread(paths, backup_dir)
    human = msg(
        detect_language(args),
        "restore_done",
        thread_id=data["restored_thread_id"],
        backup_dir=data["pre_restore_backup_dir"],
        rollout=str(data["rollout_exists"]).lower(),
    )
    print_output(args, data, human)


def cmd_list_backups(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    records = list_backup_records(paths)
    data = {"count": len(records), "backups": records}
    human = msg(detect_language(args), "no_backups") if not records else "\n".join(
        f"{record.get('backup_dir')} | {record.get('thread_id')} | {compact_human_text(record.get('title') or '')}"
        for record in records
    )
    print_output(args, data, human)


def cmd_doctor(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    data = doctor(paths)
    status = "ok" if data["schema_ok"] and data["database_exists"] and data["index_exists"] else "not ok"
    warnings = data.get("warnings") or []
    lang = detect_language(args)
    human = msg(lang, "doctor", status=status, codex_home=paths.codex_home)
    if warnings:
        human += "\n" + msg(lang, "warnings") + "\n" + "\n".join(f"- {warning}" for warning in warnings)
    print_output(args, data, human)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit stable JSON for automation")
    parser.add_argument("--lang", choices=("auto", LANG_EN, LANG_ZH), default="auto", help="Human output language")
    parser.add_argument("--codex-home", help="Override CODEX_HOME for this invocation")
    sub = parser.add_subparsers(dest="command", required=True)

    find = sub.add_parser("find", help="Find candidate Codex conversation threads")
    find.add_argument("--id")
    find.add_argument("--current", action="store_true", help="Use CODEX_THREAD_ID as the target id")
    find.add_argument("--title")
    find.add_argument("--exact", action="store_true")
    find.add_argument("--cwd")
    find.add_argument("--limit", type=int, default=20)
    find.set_defaults(func=cmd_find)

    delete = sub.add_parser("delete", help="Back up and delete one confirmed thread")
    target = delete.add_mutually_exclusive_group(required=True)
    target.add_argument("--id")
    target.add_argument("--current", action="store_true", help="Use CODEX_THREAD_ID as the target id")
    delete.add_argument("--dry-run", action="store_true", help="Show the delete plan without writing or deleting")
    delete.add_argument("--confirm", help="Required for real deletion; must equal the target thread id")
    delete.set_defaults(func=cmd_delete)

    restore = sub.add_parser("restore", help="Restore one deleted thread from a backup directory")
    restore.add_argument("--backup-dir", required=True)
    restore.set_defaults(func=cmd_restore)

    list_cmd = sub.add_parser("list-backups", help="List deletion backups")
    list_cmd.set_defaults(func=cmd_list_backups)

    doctor_cmd = sub.add_parser("doctor", help="Inspect Codex local state and required schema")
    doctor_cmd.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except CliError as exc:
        lang = detect_language(args)
        text = localized_error(exc, lang)
        if args.json:
            print(as_json({"error": text, "changed_anything": False}), file=sys.stderr)
        else:
            print(f"{msg(lang, 'error_prefix')}: {text}", file=sys.stderr)
        return 2
    except Exception as exc:
        lang = detect_language(args)
        if args.json:
            print(as_json({"error": str(exc), "changed_anything": False}), file=sys.stderr)
        else:
            print(f"{msg(lang, 'unexpected_prefix')}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
