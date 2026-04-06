# Plan 3: Writer Agent + Obsidian 写入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次 Socratic 对话结束时，自动生成结构化 Markdown 笔记，通过飞书发给用户，同时存入服务器 `notes/` 目录；Tracy 本地运行同步脚本，把笔记拉取到 Obsidian。

**Architecture:** SOUL.md 新增 Writer 模块——对话进入收尾阶段（阶段三完成或 conversation_end）时，AI 按固定模板生成 Obsidian 兼容的 Markdown 笔记，直接写文件到 `/root/.openclaw/workspace/notes/`，并把笔记内容通过飞书发给用户。Tracy 的 Mac 运行 `sync-notes.sh` 脚本（手动触发或 cron），用 scp 把服务器上的 notes 拉取到本地 Obsidian Vault。

**Tech Stack:** OpenClaw 文件写入工具（AI 自带，已用于写 memory/ 目录）、SSH/SCP（本地同步）、Obsidian Vault 本地路径（环境变量 `OBSIDIAN_VAULT_PATH` 配置）

**服务器 SSH：** `ubuntu@106.54.167.30`，私钥 `/Users/tracy_t/Downloads/openclaw_0404.pem`，工作目录 `/root/.openclaw/workspace/`

---

## 文件结构

| 文件路径 | 类型 | 说明 |
|---|---|---|
| `/root/.openclaw/workspace/SOUL.md` | 修改 | 新增 Writer 模块：笔记生成时机 + 格式模板 + 写文件指令 |
| `/root/.openclaw/workspace/notes/` | 新建目录 | 服务器端笔记存档（每次对话生成一个 .md 文件） |
| `/Users/tracy_t/Desktop/第二大脑Crucible项目/sync-notes.sh` | 新建 | 本地同步脚本：把服务器 notes/ 拉取到 Obsidian |

---

## Task 1: 新建服务器 notes 目录

**Files:**
- Create dir: `/root/.openclaw/workspace/notes/`

**Goal:** 给 Writer 一个存笔记的地方，并验证 AI 有写权限。

- [ ] **Step 1: 创建目录**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo mkdir -p /root/.openclaw/workspace/notes && sudo chmod 777 /root/.openclaw/workspace/notes && echo 'notes dir ready'"
```

Expected: `notes dir ready`

- [ ] **Step 2: 写入测试文件，验证写权限**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "echo '# test' | sudo tee /root/.openclaw/workspace/notes/test.md > /dev/null && sudo ls /root/.openclaw/workspace/notes/"
```

Expected: `test.md`

- [ ] **Step 3: 删除测试文件**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo rm /root/.openclaw/workspace/notes/test.md && echo 'cleaned'"
```

Expected: `cleaned`

- [ ] **Step 4: Commit**

```bash
cd "/Users/tracy_t/Desktop/第二大脑Crucible项目"
git add Plan3_Writer_Obsidian_2026-04-04.md
git commit -m "plan: add Plan 3 Writer + Obsidian sync implementation plan"
```

---

## Task 2: 更新 SOUL.md，加入 Writer 模块

**Files:**
- Modify: `/root/.openclaw/workspace/SOUL.md`

**Goal:** 对话结束时，AI 自动生成结构化笔记，写入 `notes/` 目录，并把笔记内容发给用户。

**笔记触发时机：**
- 阶段三用户给出洞察后（AI 提炼完一句话洞察，完整收尾）
- 用户主动说「先到这里」等 conversation_end 关键词时

**笔记格式（Obsidian 兼容）：**

```markdown
---
date: YYYY-MM-DD
type: insight
source: 飞书对话
tags: [crucible]
---

# [一句话洞察作为标题]

## 原始触发
[用户发来的第一条内容，原文]

## 追问摘要
[1-3 句话概括追问中的关键转折，不超过 50 字]

## 洞察
> [用户的一句话洞察，原文引用]

## 个人应用场景
[用户在对话中提到的具体场景，没有则写「待补充」]
```

- [ ] **Step 1: 读取现有 SOUL.md 内容**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo cat /root/.openclaw/workspace/SOUL.md"
```

- [ ] **Step 2: 在 SOUL.md 末尾追加 Writer 模块**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo tee -a /root/.openclaw/workspace/SOUL.md > /dev/null" << 'ENDSSH'

---

## 第三步：生成笔记（Writer）

**触发条件：**
- 阶段三用户给出洞察，你完成「✅ 这次追问到这里」的收尾回复之后
- 用户说了结束语（conversation_end），你完成总结回复之后

**两个动作，按顺序执行：**

### 动作一：把笔记写入文件

生成一个文件，路径：`/root/.openclaw/workspace/notes/YYYY-MM-DD-HHmm-[洞察关键词].md`

- 日期时间用当前时间（格式：2026-04-04-1430）
- 洞察关键词：取洞察句子里最核心的 2-4 个汉字（用连字符连接，如 `定义问题`）
- 文件内容按以下模板：

```
---
date: YYYY-MM-DD
type: insight
source: 飞书对话
tags: [crucible]
---

# [一句话洞察]

## 原始触发
[用户发来的第一条内容，原文]

## 追问摘要
[1-3 句话，概括追问的关键转折，不超过 50 字]

## 洞察
> [用户的一句话洞察，原文]

## 个人应用场景
[用户提到的具体场景，没有则写「待补充」]
```

### 动作二：把笔记内容发给用户

收尾回复之后，再发一条飞书消息，内容：

「📝 笔记已生成：

[完整的 Markdown 笔记内容]

已存入 Obsidian（下次同步后可见）。」

**注意：**
- 两条消息分开发：先发「✅ 这次追问到这里…」，再发「📝 笔记已生成…」
- 写文件和发消息的顺序：先写文件，再发消息
- 笔记内容只包含有价值的信息，不要把 AI 的追问过程写进去
ENDSSH
echo "exit: $?"
```

Expected: `exit: 0`

- [ ] **Step 3: 验证追加成功**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo wc -l /root/.openclaw/workspace/SOUL.md"
```

Expected: 150 行左右（原来约 95 行 + 新增约 55 行）

- [ ] **Step 4: 重启 Gateway**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo kill $(sudo ps aux | grep openclaw-gateway | grep -v grep | awk '{print $2}') 2>/dev/null; sleep 2; sudo nohup /root/.local/share/pnpm/openclaw gateway --port 39236 > /tmp/openclaw.log 2>&1 & sleep 4; sudo ps aux | grep openclaw-gateway | grep -v grep | wc -l"
```

Expected: `2`（若输出 `1` 或以上也正常，进程数因系统而异）

---

## Task 3: 验证笔记生成（飞书手动测试）

**Files:**
- 无（验证 Task 2 的实现效果）

**Goal:** 跑完一次完整对话，验证笔记正确生成并发到飞书，同时确认服务器文件已落盘。

- [ ] **Step 1: 在飞书新开对话，跑完完整 Socratic 流程**

发：
```
刚看到一个观点：好的产品经理是首席用户，不是首席需求翻译官
```

按 Plan 2 的三段式对话走完（锚定 → 深挖给具体场景 → 收尾给洞察）。

Expected：
1. Bot 发「✅ 这次追问到这里。你的洞察是：…」
2. 紧接着 Bot 发「📝 笔记已生成：[完整 Markdown 内容]」

- [ ] **Step 2: 验证服务器文件已写入**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo ls -la /root/.openclaw/workspace/notes/"
```

Expected: 出现一个 `.md` 文件，文件名含今天日期

- [ ] **Step 3: 查看笔记文件内容**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo cat /root/.openclaw/workspace/notes/*.md"
```

Expected：YAML frontmatter 正确，四个章节都有内容，洞察章节引用了用户原话

- [ ] **Step 4: 验证 conversation_end 也触发笔记**

新开对话，发一条想法，Bot 追问后说「先到这里」。

Expected：Bot 给出总结后，也发一条「📝 笔记已生成」（哪怕对话没到阶段三，也要生成一份）

---

## Task 4: 创建本地同步脚本（Mac → Obsidian）

**Files:**
- Create: `/Users/tracy_t/Desktop/第二大脑Crucible项目/sync-notes.sh`

**Goal:** Tracy 在 Mac 上运行这个脚本，把服务器 notes/ 目录的笔记同步到本地 Obsidian Vault。

- [ ] **Step 1: 写入同步脚本**

```bash
cat > "/Users/tracy_t/Desktop/第二大脑Crucible项目/sync-notes.sh" << 'EOF'
#!/bin/bash
# Crucible 笔记同步脚本
# 用法：bash sync-notes.sh
# 或设置环境变量：export OBSIDIAN_VAULT_PATH=/path/to/your/vault
# 然后运行：bash sync-notes.sh

set -e

REMOTE_HOST="ubuntu@106.54.167.30"
REMOTE_NOTES="/root/.openclaw/workspace/notes/"
SSH_KEY="/Users/tracy_t/Downloads/openclaw_0404.pem"

# Obsidian Vault 路径：优先读环境变量，否则用默认值
OBSIDIAN_VAULT_PATH="${OBSIDIAN_VAULT_PATH:-$HOME/Documents/ObsidianVault}"
LOCAL_DIR="$OBSIDIAN_VAULT_PATH/Crucible"

# 创建本地目录（如果不存在）
mkdir -p "$LOCAL_DIR"

echo "同步 Crucible 笔记到 Obsidian..."
echo "远端：$REMOTE_HOST:$REMOTE_NOTES"
echo "本地：$LOCAL_DIR"
echo ""

# 主路径：从服务器拉取 .md 文件到 Crucible/ 文件夹
# Obsidian 会自动检测到新文件，无需 app 开着
scp -i "$SSH_KEY" \
    -o StrictHostKeyChecking=no \
    -o IdentitiesOnly=yes \
    "$REMOTE_HOST:$REMOTE_NOTES*.md" \
    "$LOCAL_DIR/" 2>/dev/null || echo "（没有新笔记，或目录为空）"

# 可选：如果 Obsidian CLI 可用且 app 正在运行，把洞察标题追加到今天的 Daily Note 作为索引
# 要求：Obsidian 1.12+ 且 app 开着
if command -v obsidian &>/dev/null; then
    NEW_FILES=$(ls -t "$LOCAL_DIR/"*.md 2>/dev/null | head -5)
    for f in $NEW_FILES; do
        TITLE=$(grep '^# ' "$f" | head -1 | sed 's/^# //')
        DATE=$(grep '^date:' "$f" | head -1 | sed 's/date: //')
        if [ -n "$TITLE" ]; then
            obsidian daily:append "- [[Crucible/$DATE]] $TITLE" 2>/dev/null || true
        fi
    done
    echo "（Daily Note 索引已更新）"
fi

# 显示同步结果
echo ""
echo "✅ 同步完成。本地 Crucible 目录："
ls -la "$LOCAL_DIR/"
EOF
chmod +x "/Users/tracy_t/Desktop/第二大脑Crucible项目/sync-notes.sh"
echo "sync-notes.sh created"
```

Expected: `sync-notes.sh created`

- [ ] **Step 2: 测试同步脚本**

先确认 Obsidian Vault 路径（如果不确定，先用默认路径测试）：

```bash
# 替换成你真实的 Obsidian Vault 路径再运行
OBSIDIAN_VAULT_PATH="/Users/tracy_t/Documents/ObsidianVault" bash "/Users/tracy_t/Desktop/第二大脑Crucible项目/sync-notes.sh"
```

Expected：
- 显示「同步完成」
- 显示 `Crucible/` 目录下有 .md 文件列表（来自 Task 3 测试生成的笔记）

- [ ] **Step 3: 在 Obsidian 中打开验证**

打开 Obsidian，进入 Vault → `Crucible/` 文件夹，确认笔记文件可以正常显示（YAML frontmatter、各章节内容）。

---

## Task 5: 最终验收 + Commit

**Goal:** 全流程 end-to-end 验证，然后提交所有改动。

- [ ] **Step 1: 端到端验收清单**

在飞书走完完整流程，对照以下验收项：

| 验收项 | 验证方式 |
|---|---|
| 阶段三收尾后自动生成笔记 | 飞书收到「📝 笔记已生成」消息 |
| 笔记格式正确（YAML + 4 章节）| 飞书消息内容格式检查 |
| 服务器文件落盘 | `ls /root/.openclaw/workspace/notes/` |
| conversation_end 也触发笔记 | 发「先到这里」后检查 |
| 本地同步脚本可运行 | `bash sync-notes.sh` 无报错 |
| Obsidian 可打开笔记 | Obsidian 内查看 |

- [ ] **Step 2: 更新 CLAUDE.md（标记 Plan 3 完成）**

编辑项目根目录的 `CLAUDE.md`，把 Plan 3 的复选框从 `[ ]` 改为 `[x]`：

```
- [x] **Plan 3**：Writer Agent + Obsidian 写入（笔记生成 + 文件系统操作）
```

- [ ] **Step 3: 最终 Commit**

```bash
cd "/Users/tracy_t/Desktop/第二大脑Crucible项目"
git add -A
git commit -m "feat: Plan 3 complete — Writer Agent + Obsidian sync live

SOUL.md: Writer module added — generates structured Markdown note on conversation end.
Note format: YAML frontmatter + 原始触发 + 追问摘要 + 洞察 + 个人应用场景.
Delivery: note sent to user via Feishu + archived to server notes/ directory.
sync-notes.sh: local script to pull server notes into Obsidian Vault via scp."
```

---

## Plan 3 验收标准

| 验收项 | 验证方式 |
|---|---|
| 阶段三后自动生成笔记 | 飞书收到「📝 笔记已生成」+ 完整 Markdown |
| conversation_end 也生成笔记 | 「先到这里」后同样触发 |
| 笔记格式 Obsidian 兼容 | YAML frontmatter 正确，Obsidian 可打开 |
| 服务器文件落盘 | `notes/` 目录有对应 .md 文件 |
| 本地同步到 Obsidian | sync-notes.sh 成功拉取 |

---

## 下一步

Plan 3 完成后，直接进入 **Plan 4：crucible.db 评估入库 + /stats 统计指令**：
- 每次笔记生成时，把元数据写入 SQLite 数据库（日期、标题、洞察、轮数）
- `/stats` 指令返回真实统计（已记录洞察数、本周活跃天数）
