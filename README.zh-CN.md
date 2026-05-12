# codex-delete-conversation

[English README](README.md)

安全预览、删除和恢复本机 Codex 桌面端会话。

这个仓库包含一个 Codex 技能和一个只依赖 Python 标准库的 CLI。它面向两类使用者：

- 需要可靠处理“删除当前对话”请求的 Codex agent。
- 想在终端里执行 dry-run、显式确认、备份和恢复的普通用户。

## 安全模型

真实删除默认很难误触发：

- 每次删除前都建议先 dry-run。
- 真实删除必须传入与目标一致的 `--confirm <thread-id>`。
- 如果 Codex 数据库、会话索引、数据库结构或 rollout 正文缺失，工具会拒绝删除。
- 删除前会创建本地备份，包含：
  - `state_5.sqlite`
  - `session_index.jsonl`
  - `rollout.jsonl`
  - `manifest.json`
- 如果备份完成后删除流程中途失败，CLI 会尝试从刚生成的备份恢复原状态。

备份目录位于：

```text
~/.codex/deleted_conversation_backups/<timestamp>-<thread-id>/
```

隐私提醒：备份里包含完整本地聊天正文。请把备份目录视为私密数据，不要上传到公开仓库或分享给他人。

## 安装

把技能文件夹复制到 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
cp -R skills/codex-delete-conversation ~/.codex/skills/
```

CLI 没有第三方依赖，支持 Python 3.9+。

## 使用

检查环境：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py doctor
```

查找会话：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py find --title "项目记录"
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py find --id "<thread-id>"
```

在 Codex 里预览删除当前会话：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --current --dry-run
```

按 ID 预览删除：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --id "<thread-id>" --dry-run
```

真实删除：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py delete --id "<thread-id>" --confirm "<thread-id>"
```

恢复：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py list-backups
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py restore --backup-dir "<backup-dir>"
```

给自动化或测试使用稳定 JSON 输出时，把 `--json` 放在子命令前：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py --json delete --id "<thread-id>" --dry-run
```

强制中文人类可读输出时，把 `--lang zh-CN` 放在子命令前：

```bash
python3 ~/.codex/skills/codex-delete-conversation/scripts/codex_conversation_delete.py --lang zh-CN delete --current --dry-run
```

默认 `--lang auto` 会读取 `LANG`、`LC_ALL`、`LC_MESSAGES`。例如 `zh_CN.UTF-8` 会自动使用中文人类输出。

## 环境变量

- `CODEX_HOME`：覆盖默认 Codex 数据目录，默认是 `~/.codex`。
- `CODEX_THREAD_ID`：`--current` 用它识别当前线程。
- `LANG`、`LC_ALL`、`LC_MESSAGES`：`--lang auto` 时用于选择人类输出语言。

这个工具只处理本机 Codex 状态，不会删除云端同步副本。

## 开发

运行测试：

```bash
python3 -m unittest discover -s tests
```

语法检查：

```bash
python3 -m py_compile skills/codex-delete-conversation/scripts/codex_conversation_delete.py
```

如果本机有 Codex 的 skill creator 校验器，可以验证技能结构：

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/codex-delete-conversation
```

## 许可证

MIT
