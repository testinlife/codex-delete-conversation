# codex-delete-conversation

[中文说明](README.zh-CN.md)

Safely preview, delete, and restore local Codex desktop conversations.

This repository contains a Codex skill plus a small standard-library Python CLI. It is designed for two audiences:

- Codex agents that need a reliable workflow for "delete this conversation" requests.
- Humans who want a terminal command with dry-run, explicit confirmation, backups, and restore.

## Safety Model

Real deletion is intentionally hard to trigger:

- Dry-run is supported and recommended before every deletion.
- Real deletion requires `--confirm <thread-id>` matching the target.
- The tool refuses to delete when the Codex database, session index, schema, or rollout transcript is missing.
- Before deletion, it creates a local backup containing:
  - `state_5.sqlite`
  - `session_index.jsonl`
  - `rollout.jsonl`
  - `manifest.json`
- If a deletion step fails after backup creation, the CLI tries to restore the original state from that backup.

Backups are stored under:

```text
~/.codex/deleted_conversation_backups/<timestamp>-<thread-id>/
```

Privacy note: backups contain the full local conversation transcript. Treat backup folders as private data.

## Install

Copy the skill folder into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
cp -R skills/codex-delete-conversation ~/.codex/skills/
```

The CLI has no third-party dependencies and supports Python 3.9+.

## Usage

Run a health check:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py doctor
```

Find a conversation:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py find --title "project notes"
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py find --id "<thread-id>"
```

Preview deleting the current conversation from inside Codex:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --current --dry-run
```

Preview deleting by id:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --id "<thread-id>" --dry-run
```

Actually delete:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --id "<thread-id>" --confirm "<thread-id>"
```

Restore:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py list-backups
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py restore --backup-dir "<backup-dir>"
```

Use `--json` before the subcommand for stable machine-readable output:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py --json delete --id "<thread-id>" --dry-run
```

Use `--lang zh-CN` before the subcommand for Chinese human-readable output:

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py --lang zh-CN delete --current --dry-run
```

## Environment

- `CODEX_HOME`: Overrides the default Codex data directory. Defaults to `~/.codex`.
- `CODEX_THREAD_ID`: Used by `--current` to identify the active thread.
- `LANG`, `LC_ALL`, `LC_MESSAGES`: When `--lang auto` is used, Chinese locales such as `zh_CN.UTF-8` select Chinese human-readable output.

The tool only manipulates local Codex state. It does not delete cloud-synced copies.

## Development

Run tests:

```bash
python3 -m unittest discover -s tests
```

Run a syntax check:

```bash
python3 -m py_compile skills/codex-delete-conversation/scripts/codex_conversation_delete.py
```

Validate the skill with Codex's skill creator validator when available:

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/codex-delete-conversation
```

## License

MIT
