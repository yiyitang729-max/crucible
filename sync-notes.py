#!/usr/bin/env python3
"""
Crucible 笔记同步 + 评估入库脚本

用法：
    python3 sync-notes.py           # 同步笔记 + 更新 crucible.db
    python3 sync-notes.py --stats   # 显示统计摘要
    OBSIDIAN_VAULT_PATH=/path/to/vault python3 sync-notes.py
"""

import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────
APP_ID = "cli_a945df04abb8dbcd"
APP_SECRET = "HU2Vo2kQDhW5Rbd54eBsZcN3SGJsxnyb"
CHAT_ID = "oc_55b08d01b21e0343f55e63deb917ea59"

VAULT_PATH = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", os.path.expanduser("~/Documents/ObsidianVault"))
)
NOTE_DIR = VAULT_PATH / "Crucible"
STATE_FILE = NOTE_DIR / ".sync-state.json"
DB_PATH = VAULT_PATH / "crucible.db"

# 服务器 SSH（用于上传 stats）
SSH_KEY = "/Users/tracy_t/Downloads/openclaw_0404.pem"
REMOTE_HOST = "ubuntu@106.54.167.30"
REMOTE_STATS = "/root/.openclaw/workspace/STATS.md"


# ── 飞书 API ─────────────────────────────────────────
def get_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["tenant_access_token"]


def list_messages(token, page_token=None):
    url = (
        f"https://open.feishu.cn/open-apis/im/v1/messages"
        f"?container_id_type=chat&container_id={CHAT_ID}"
        f"&page_size=50&sort_type=ByCreateTimeDesc"
    )
    if page_token:
        url += f"&page_token={page_token}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_all_messages(token, max_pages=10):
    """拉取所有消息（按时间倒序），返回列表。"""
    all_items = []
    page_token = None
    for _ in range(max_pages):
        result = list_messages(token, page_token)
        items = result.get("data", {}).get("items", [])
        all_items.extend(items)
        if not result.get("data", {}).get("has_more"):
            break
        page_token = result.get("data", {}).get("page_token")
    return all_items


# ── 消息解析 ──────────────────────────────────────────
def extract_text(item):
    """从消息中提取纯文本。"""
    msg_type = item.get("msg_type", "")
    raw = item.get("body", {}).get("content", "")
    if msg_type == "text":
        try:
            return json.loads(raw).get("text", "")
        except (json.JSONDecodeError, TypeError):
            return ""
    elif msg_type == "post":
        return extract_text_from_post(raw)
    return ""


def extract_text_from_post(content_json):
    """把飞书 post 类型的 rich text 转成纯文本。"""
    try:
        data = json.loads(content_json) if isinstance(content_json, str) else content_json
    except (json.JSONDecodeError, TypeError):
        return ""
    lines = []
    for paragraph in data.get("content", []):
        parts = []
        for elem in paragraph:
            tag = elem.get("tag", "")
            if tag == "text":
                parts.append(elem.get("text", ""))
            elif tag == "hr":
                parts.append("---")
        lines.append("".join(parts))
    return "\n".join(lines)


def is_crucible_note(text):
    return "Crucible 笔记" in text


def is_user_msg(item):
    return item.get("sender", {}).get("sender_type") == "user"


def is_bot_msg(item):
    return item.get("sender", {}).get("sender_type") == "app"


def parse_note(text, msg_id, create_time_ms):
    """从纯文本中解析出笔记的各个字段。"""
    date_match = re.search(r"日期[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

    insight_match = re.search(r"洞察[：:]\s*(.+?)(?:\n|$)", text)
    insight = insight_match.group(1).strip() if insight_match else "未提取到洞察"

    trigger_match = re.search(r"原始触发\s*\n(.+?)(?:\n|$)", text)
    trigger = trigger_match.group(1).strip() if trigger_match else ""

    summary_match = re.search(r"追问摘要\s*\n(.+?)(?:\n|$)", text)
    summary = summary_match.group(1).strip() if summary_match else ""

    scene_match = re.search(r"个人应用场景\s*\n(.+?)(?:\n|$)", text)
    scene = scene_match.group(1).strip() if scene_match else "待补充"

    closing_match = re.search(r"洞察是[：:]\s*(.+?)(?:[。\n]|$)", text)
    title_insight = closing_match.group(1).strip() if closing_match else insight

    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title_insight)[:30].strip("-")
    filename = f"{date}-{slug}.md"

    md = f"""---
date: {date}
type: insight
source: 飞书对话
tags: [crucible]
feishu_msg_id: {msg_id}
---

# {title_insight}

## 原始触发
{trigger}

## 追问摘要
{summary}

## 洞察
> {insight}

## 个人应用场景
{scene}
"""
    return filename, md, {
        "date": date,
        "insight": insight,
        "trigger": trigger,
        "scene": scene,
    }


# ── 对话分段 ──────────────────────────────────────────
def segment_conversations(messages):
    """
    把消息流分段成独立对话。
    策略：从笔记消息往回追溯，找到对应的用户第一条消息。
    两条消息间隔超过 30 分钟视为不同对话。
    返回 list of dict，每个 dict 包含对话元数据。
    """
    # 按时间正序排列
    sorted_msgs = sorted(messages, key=lambda m: int(m.get("create_time", "0")))

    conversations = []
    current_conv = []
    last_time = 0

    for msg in sorted_msgs:
        msg_time = int(msg.get("create_time", "0"))
        text = extract_text(msg)

        # 跳过系统消息、空消息
        if not text.strip():
            continue
        # 跳过 HTTP 500 错误消息
        if "HTTP 500" in text or "api_error" in text:
            continue

        # 间隔超过 30 分钟 → 新对话
        if last_time > 0 and (msg_time - last_time) > 1800 * 1000:
            if current_conv:
                conversations.append(current_conv)
            current_conv = []

        current_conv.append(msg)
        last_time = msg_time

    if current_conv:
        conversations.append(current_conv)

    return conversations


def analyze_conversation(msgs):
    """分析一段对话，提取指标。"""
    user_msgs = [m for m in msgs if is_user_msg(m)]
    bot_msgs = [m for m in msgs if is_bot_msg(m)]

    # 对话 ID：用第一条消息的 message_id
    conv_id = msgs[0].get("message_id", "") if msgs else ""

    # 时间
    first_time = int(msgs[0].get("create_time", "0")) if msgs else 0
    created_at = datetime.fromtimestamp(first_time / 1000).isoformat() if first_time else ""

    # 用户消息总字数
    user_chars = sum(len(extract_text(m)) for m in user_msgs)

    # 轮数：用户消息数（第一条是触发，后面是追问回复）
    turns = len(user_msgs)

    # 输入类型
    first_user_text = extract_text(user_msgs[0]) if user_msgs else ""
    if re.search(r"https?://", first_user_text):
        input_type = "article"
    else:
        input_type = "thought"

    # 是否完成（有笔记 = 完成）
    completed = False
    note_msg_id = None
    has_application = False

    for m in bot_msgs:
        text = extract_text(m)
        if is_crucible_note(text):
            completed = True
            note_msg_id = m.get("message_id", "")
            # 检查个人应用场景
            scene_match = re.search(r"个人应用场景\s*\n(.+?)(?:\n|$)", text)
            if scene_match:
                scene = scene_match.group(1).strip()
                has_application = scene not in ("待补充", "")

    return {
        "id": conv_id,
        "created_at": created_at,
        "input_type": input_type,
        "turns": turns,
        "completed": completed,
        "has_application": has_application,
        "user_msg_chars": user_chars,
        "note_id": note_msg_id,
    }


# ── SQLite ────────────────────────────────────────────
def init_db():
    """初始化 crucible.db。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_metrics (
            id              TEXT PRIMARY KEY,
            created_at      DATETIME,
            input_type      TEXT,
            turns           INTEGER,
            completed       BOOLEAN,
            has_application BOOLEAN,
            user_msg_chars  INTEGER,
            note_id         TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_metrics (
            note_id         TEXT PRIMARY KEY,
            created_at      DATETIME,
            linked_count    INTEGER DEFAULT 0,
            searched_hit    INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def upsert_conversation(conn, metrics):
    conn.execute("""
        INSERT INTO conversation_metrics
            (id, created_at, input_type, turns, completed, has_application, user_msg_chars, note_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            turns = excluded.turns,
            completed = excluded.completed,
            has_application = excluded.has_application,
            user_msg_chars = excluded.user_msg_chars,
            note_id = excluded.note_id
    """, (
        metrics["id"], metrics["created_at"], metrics["input_type"],
        metrics["turns"], metrics["completed"], metrics["has_application"],
        metrics["user_msg_chars"], metrics["note_id"],
    ))


def upsert_note(conn, note_id, created_at):
    conn.execute("""
        INSERT INTO note_metrics (note_id, created_at)
        VALUES (?, ?)
        ON CONFLICT(note_id) DO NOTHING
    """, (note_id, created_at))


# ── 同步状态 ──────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"synced_msg_ids": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── 统计 ──────────────────────────────────────────────
def show_stats():
    """显示统计摘要。"""
    if not DB_PATH.exists():
        print("还没有数据。先运行 python3 sync-notes.py 同步一次。")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 总览
    total = conn.execute("SELECT COUNT(*) FROM conversation_metrics").fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM conversation_metrics WHERE completed = 1").fetchone()[0]
    has_app = conn.execute("SELECT COUNT(*) FROM conversation_metrics WHERE has_application = 1").fetchone()[0]
    avg_turns = conn.execute("SELECT AVG(turns) FROM conversation_metrics").fetchone()[0] or 0
    total_notes = conn.execute("SELECT COUNT(*) FROM note_metrics").fetchone()[0]

    # 最近 7 天
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    week_total = conn.execute(
        "SELECT COUNT(*) FROM conversation_metrics WHERE created_at >= ?", (week_ago,)
    ).fetchone()[0]
    week_completed = conn.execute(
        "SELECT COUNT(*) FROM conversation_metrics WHERE created_at >= ? AND completed = 1", (week_ago,)
    ).fetchone()[0]
    week_app = conn.execute(
        "SELECT COUNT(*) FROM conversation_metrics WHERE created_at >= ? AND has_application = 1", (week_ago,)
    ).fetchone()[0]

    # 活跃天数（最近 30 天）
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()
    active_days = conn.execute("""
        SELECT COUNT(DISTINCT DATE(created_at))
        FROM conversation_metrics WHERE created_at >= ?
    """, (month_ago,)).fetchone()[0]

    conn.close()

    completion_rate = (completed / total * 100) if total > 0 else 0
    app_rate = (has_app / total * 100) if total > 0 else 0
    week_comp_rate = (week_completed / week_total * 100) if week_total > 0 else 0

    print("=" * 40)
    print("📊 Crucible 统计")
    print("=" * 40)
    print()
    print(f"【总览】")
    print(f"  对话总数：{total}")
    print(f"  完成收尾：{completed}（{completion_rate:.0f}%）")
    print(f"  有应用场景：{has_app}（{app_rate:.0f}%）")
    print(f"  平均轮数：{avg_turns:.1f}")
    print(f"  笔记总数：{total_notes}")
    print()
    print(f"【最近 7 天】")
    print(f"  对话：{week_total}，完成：{week_completed}（{week_comp_rate:.0f}%）")
    print(f"  有应用场景：{week_app}")
    print()
    print(f"【最近 30 天】")
    print(f"  活跃天数：{active_days}")
    print()

    # 生成 stats 文本用于上传到服务器
    stats_md = generate_stats_md(
        total, completed, completion_rate, has_app, app_rate,
        avg_turns, total_notes, week_total, week_completed,
        week_comp_rate, week_app, active_days,
    )
    return stats_md


def generate_stats_md(total, completed, comp_rate, has_app, app_rate,
                      avg_turns, total_notes, week_total, week_completed,
                      week_comp_rate, week_app, active_days):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""📊 Crucible 统计（更新于 {now}）

【总览】
- 对话总数：{total}
- 完成收尾：{completed}（{comp_rate:.0f}%）
- 有应用场景：{has_app}（{app_rate:.0f}%）
- 平均轮数：{avg_turns:.1f}
- 笔记总数：{total_notes}

【最近 7 天】
- 对话：{week_total}，完成：{week_completed}（{week_comp_rate:.0f}%）
- 有应用场景：{week_app}

【最近 30 天活跃天数】{active_days}
"""


def upload_stats_to_server(stats_md):
    """把统计摘要上传到服务器，让 bot /stats 能读到。"""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(stats_md)
        tmp_path = f.name

    try:
        # scp 上传到服务器
        subprocess.run([
            "scp", "-i", SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "IdentitiesOnly=yes",
            tmp_path, f"{REMOTE_HOST}:/tmp/crucible-stats.md",
        ], capture_output=True, timeout=10)

        subprocess.run([
            "ssh", "-i", SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "IdentitiesOnly=yes",
            REMOTE_HOST,
            f"sudo cp /tmp/crucible-stats.md {REMOTE_STATS}",
        ], capture_output=True, timeout=10)

        print("✓ 统计已上传到服务器")
    except Exception as e:
        print(f"⚠ 统计上传失败（不影响本地数据）: {e}")
    finally:
        os.unlink(tmp_path)


# ── 主流程 ────────────────────────────────────────────
def sync():
    NOTE_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    synced = set(state.get("synced_msg_ids", []))

    print(f"Obsidian vault: {VAULT_PATH}")
    print(f"笔记目录: {NOTE_DIR}")
    print(f"数据库: {DB_PATH}")
    print(f"已同步笔记: {len(synced)} 条\n")

    token = get_token()
    print("✓ 获取飞书 token 成功")

    # 拉取所有消息
    all_messages = fetch_all_messages(token)
    print(f"✓ 拉取 {len(all_messages)} 条消息")

    # ── 笔记同步 ──
    new_notes = []
    for item in all_messages:
        msg_id = item.get("message_id", "")
        if msg_id in synced:
            continue
        if not is_bot_msg(item):
            continue
        if item.get("msg_type") != "post":
            continue

        text = extract_text_from_post(item.get("body", {}).get("content", ""))
        if not is_crucible_note(text):
            continue

        create_time = item.get("create_time", "0")
        filename, md, note_fields = parse_note(text, msg_id, create_time)
        new_notes.append((msg_id, filename, md, note_fields))

    if new_notes:
        for msg_id, filename, md, _ in new_notes:
            filepath = NOTE_DIR / filename
            if filepath.exists():
                base = filepath.stem
                for i in range(2, 100):
                    filepath = NOTE_DIR / f"{base}-{i}.md"
                    if not filepath.exists():
                        break
            filepath.write_text(md, encoding="utf-8")
            synced.add(msg_id)
            print(f"  📝 {filepath.name}")

        state["synced_msg_ids"] = list(synced)
        state["last_sync"] = datetime.now().isoformat()
        save_state(state)
        print(f"\n✅ 新增 {len(new_notes)} 条笔记")
    else:
        print("\n没有新笔记。")

    # ── 对话分析 + 入库 ──
    print("\n分析对话指标...")
    conversations = segment_conversations(all_messages)
    print(f"✓ 识别出 {len(conversations)} 段对话")

    conn = init_db()
    conv_count = 0
    note_count = 0

    for conv_msgs in conversations:
        metrics = analyze_conversation(conv_msgs)
        if not metrics["id"]:
            continue
        upsert_conversation(conn, metrics)
        conv_count += 1

        if metrics["note_id"]:
            upsert_note(conn, metrics["note_id"], metrics["created_at"])
            note_count += 1

    conn.commit()
    conn.close()
    print(f"✓ 写入 {conv_count} 条对话指标，{note_count} 条笔记指标")

    # ── 上传统计到服务器 ──
    stats_md = show_stats()
    if stats_md:
        upload_stats_to_server(stats_md)


def main():
    if "--stats" in sys.argv:
        show_stats()
    else:
        sync()


if __name__ == "__main__":
    main()
