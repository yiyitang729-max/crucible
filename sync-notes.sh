#!/bin/bash
# Crucible 笔记同步（wrapper）
# 实际逻辑在 sync-notes.py，本脚本用于 cron 调用
# 用法：bash sync-notes.sh
# 或设置环境变量：export OBSIDIAN_VAULT_PATH=/path/to/your/vault

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/sync-notes.py"
