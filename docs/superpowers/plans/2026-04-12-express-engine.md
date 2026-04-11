# Express Engine (V4) Implementation Plan

**Goal:** 闭合 CODE 循环的最后一环——当知识成熟时，主动触发输出，生成小红书格式草稿，通过 MCP 直接发布，发布后归档反哺知识库。

**核心理念:** 输出不是存在本地，是发到外面获得正反馈。正反馈打破完美主义的输出恐惧，形成「输出→反馈→信心→更多输出」的正循环。

**Tech Stack:** Python 3, SQLite, xiaohongshu-mcp (Go/Docker), JSON reports, SCP upload

---

## Scope Check

V4 分三个 Plan，逐步构建：

1. **Plan 12: 成熟触发 + 草稿生成** — 降低阈值，生成小红书格式草稿
2. **Plan 13: 小红书发布** — 集成 xiaohongshu-mcp，一键发布
3. **Plan 14: 输出归档** — 发布内容回写知识库，闭合循环

Plan 12 可独立工作（生成草稿让用户手动复制发布）。Plan 13 依赖 Plan 12 的草稿。Plan 14 依赖 Plan 13 的发布结果。

---

## File Structure

| File | Responsibility |
|------|---------------|
| `sync-notes.py` (modify) | 降低成熟阈值、EXPRESS_REPORT 生成、输出归档逻辑、新 CLI 命令 |
| `test_sync_notes.py` (modify) | V4 测试 |
| Server: `EXPRESS_REPORT.json` (new) | 可输出的成熟主题 + 聚合素材 |
| Server: SOUL.md (modify) | `/express` 指令 + 主动提醒逻辑 |
| Server: xiaohongshu-mcp (new) | Docker 部署的小红书 MCP 服务 |

---

## Plan 12: 成熟触发 + 草稿生成

**What it does:** 当某个主题的知识成熟度达到触发阈值时，自动准备输出素材，Bot 可以生成小红书格式的草稿。

### Task 1: 降低成熟触发阈值

**Files:**
- Modify: `sync-notes.py` — `compute_maturity_level` 函数
- Test: `test_sync_notes.py`

- [ ] **Step 1: 修改成熟度计算**
  - 当前 mature 条件：`conv_count >= 4 and app_count >= 2 and linked_count >= 2`（太严格，当前数据没有 mature）
  - 新增 express_ready 标记：growing 阶段且 score >= 0.35 即可触发输出建议
  - 不改变 mature 的定义（保留用于未来），而是新增一个 `express_ready` 字段

- [ ] **Step 2: 写测试**
  - growing + score >= 0.35 → express_ready = True
  - seed → express_ready = False
  - mature → express_ready = True

### Task 2: 生成 EXPRESS_REPORT.json

**Files:**
- Modify: `sync-notes.py`
- Test: `test_sync_notes.py`

- [ ] **Step 1: 实现 `build_express_report(conn)`**
  - 从 tag_maturity 表找出 express_ready = True 的标签
  - 对每个标签，聚合其下所有笔记的：洞察、追问摘要、应用场景、原始触发
  - 生成报告结构：
    ```json
    {
      "updated_at": "...",
      "express_ready_count": 2,
      "topics": [
        {
          "tag": "知识管理",
          "maturity": "growing",
          "score": 0.44,
          "note_count": 3,
          "insights": ["洞察1", "洞察2", "洞察3"],
          "scenarios": ["场景1"],
          "summaries": ["摘要1", "摘要2"],
          "draft_prompt": "基于以上素材生成小红书笔记的 prompt"
        }
      ]
    }
    ```

- [ ] **Step 2: 在 sync() 中调用并上传**
  - build_express_report → JSON → upload_file_to_server

- [ ] **Step 3: 写测试**
  - 有 express_ready 标签 → 生成报告
  - 无 express_ready → 返回 None
  - 报告包含正确的笔记聚合数据

### Task 3: SOUL.md 新增 `/express` 指令

**Files:**
- Server: SOUL.md

- [ ] **Step 1: 添加指令逻辑**
  - `/express` → 读取 EXPRESS_REPORT.json
  - 如果有可输出主题：展示列表，问用户「想从哪个开始？」
  - 用户选择后 → 聚合该主题所有笔记素材，生成小红书格式草稿：
    - 标题（≤20字，吸引点击）
    - 正文（≤1000字，小红书风格：口语化 + emoji + 分段 + 个人经验感）
    - 标签（#话题标签，3-5个）
  - 生成后问：「这个草稿可以吗？要调整什么？还是直接发？」

- [ ] **Step 2: HEARTBEAT 主动提醒**
  - HEARTBEAT.md 中加入：检查 EXPRESS_REPORT.json，有新的 express_ready 主题时主动提醒
  - 提醒话术：「你在「XX」方向已经聊了 N 次，想法挺成熟了——要不要写成小红书发出去？」

### Task 4: 新增 `--express` CLI 命令

**Files:**
- Modify: `sync-notes.py`

- [ ] **Step 1: `python3 sync-notes.py --express`**
  - 本地输出可输出的主题列表
  - 展示每个主题的素材摘要

---

## Plan 13: 小红书发布

**What it does:** 通过 xiaohongshu-mcp 实现一键发布到小红书。

**前置条件:** xiaohongshu-mcp Docker 服务部署完毕 + 小红书 Cookie 登录

### Task 5: 部署 xiaohongshu-mcp

- [ ] **Step 1: 在服务器上部署**
  - Docker Compose 启动 xiaohongshu-mcp
  - 端口 18060，MCP endpoint: `http://localhost:18060/mcp`
  - 首次运行配置小红书 Cookie

- [ ] **Step 2: 验证连通性**
  - 测试搜索能力
  - 测试发布能力（发一条测试笔记）

### Task 6: SOUL.md 集成发布流程

**Files:**
- Server: SOUL.md

- [ ] **Step 1: 发布确认流程**
  - 用户看到草稿后说「发」「发布」「可以」→ 调用 xiaohongshu-mcp 发布
  - 发布参数：title（≤20字）、desc（≤1000字）、图片（可选，后续支持）
  - 发布成功 → 回复「已发布到小红书 ✅ [链接]」
  - 发布失败 → 回复「发布失败了，你可以先复制草稿手动发。[展示草稿]」

- [ ] **Step 2: 草稿修改流程**
  - 用户说「改一下标题」「正文太长了」→ Bot 调整后重新展示
  - 用户满意后再发布

### Task 7: 写测试

- [ ] 草稿格式符合小红书限制（标题≤20字、正文≤1000字）
- [ ] 发布失败时 fallback 正确

---

## Plan 14: 输出归档

**What it does:** 发布的内容自动归档为新笔记，闭合 CODE 循环。

### Task 8: 发布记录表

**Files:**
- Modify: `sync-notes.py`

- [ ] **Step 1: 新增 `express_log` 表**
  ```sql
  CREATE TABLE express_log (
    id INTEGER PRIMARY KEY,
    tag TEXT NOT NULL,
    title TEXT,
    content TEXT,
    platform TEXT DEFAULT 'xiaohongshu',
    published_at TEXT,
    source_note_ids TEXT,  -- JSON array
    xhs_url TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );
  ```

- [ ] **Step 2: 发布成功后写入记录**

### Task 9: 输出归档为新笔记

**Files:**
- Modify: `sync-notes.py`

- [ ] **Step 1: 实现归档逻辑**
  - 每次 sync 检查 express_log 中未归档的记录
  - 为每条发布记录生成 Obsidian 笔记（标注来源笔记 + 发布链接）
  - frontmatter 包含 `type: express`、`source_notes: [...]`、`platform: xiaohongshu`

- [ ] **Step 2: 写测试**

---

## 小红书内容风格指南（写入 SOUL.md）

生成小红书草稿时遵循以下风格：

**标题（≤20字）：**
- 用「我」开头的个人体验视角
- 带数字或对比增加点击欲
- 例：「我用AI管理知识后，再也不焦虑了」「3个月不回看收藏，我终于想通了」

**正文（≤1000字）：**
- 开头用个人故事/痛点切入（不要上来就讲道理）
- 分段短句，每段 2-3 行
- 适当用 emoji 做视觉分隔（不过度）
- 结尾有行动号召或提问（引导评论互动）
- 口语化，像在跟朋友分享

**标签（3-5个）：**
- 混合大话题 + 小话题：#知识管理 #个人成长 #AI工具 #学习方法 #第二大脑
