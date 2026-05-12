---
name: codex-delete-conversation
description: Safely delete and restore local Codex desktop conversations/threads with English and Chinese user-facing output. Use when a user asks to delete/remove the current Codex conversation, delete a Codex chat by title or thread id, preview a safe deletion, inspect deletion backups, restore a deleted Codex conversation backup, or says Chinese phrases like "删除当前对话", "帮我删除这个会话", "恢复删除的 Codex 对话".
---

# Codex Delete Conversation

Use the bundled CLI. It protects the Codex database, conversation index, and rollout transcript before any real deletion.

The CLI supports localized human output. Add `--lang zh-CN` before the subcommand for Chinese, or rely on `--lang auto` with a Chinese locale. Keep `--json` for stable automation output.

## Standard Workflow

1. Locate the target. Prefer `--current` when the user asks for the current conversation; otherwise find by title and confirm the exact thread id if multiple candidates appear.

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py find --current
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py find --title "<title text>"
```

2. Preview first. Dry runs must report `changed_anything=false`.

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --current --dry-run
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --id "<thread-id>" --dry-run
```

3. Delete only after the target is unambiguous. Real deletion requires an exact confirmation id.

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --id "<thread-id>" --confirm "<thread-id>"
```

4. For automation or tests, add `--json`. For Chinese human output, add `--lang zh-CN`.

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py --lang zh-CN delete --current --dry-run
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py --json delete --current --dry-run
```

## Restore

Backups are local and include the full conversation transcript. Restore only when the user asks to undo a deletion.

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py list-backups
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py restore --backup-dir "<backup-dir>"
```

## Guardrails

- Never skip dry-run for a destructive user request unless the user explicitly provides the exact thread id and asks for immediate deletion.
- Never delete similarly named threads without confirmation.
- Never run real deletion without `--confirm <thread-id>`.
- Tell users that backup folders contain private chat content.
- Answer Chinese users in Chinese and use `--lang zh-CN` for human-readable CLI output.
- Use `doctor` when the local Codex schema or paths look unusual.
