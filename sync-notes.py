#!/usr/bin/env python3
"""
Crucible 笔记同步 + 评估入库脚本

用法：
    python3 sync-notes.py           # 同步笔记 + 更新 crucible.db
    python3 sync-notes.py --stats   # 显示统计摘要
    OBSIDIAN_VAULT_PATH=/path/to/vault python3 sync-notes.py
"""

import json
import logging
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── 日志配置 ─────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"sync-{datetime.now().strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("crucible")

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
REMOTE_TAG_INDEX = "/root/.openclaw/workspace/TAG_INDEX.json"
REMOTE_DIGEST_REPORT = "/root/.openclaw/workspace/DIGEST_REPORT.json"
REMOTE_MATURITY_REPORT = "/root/.openclaw/workspace/MATURITY_REPORT.json"


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

    # 标签（V2 新增）
    tags_match = re.search(r"标签[：:]\s*(.+?)(?:\n|$)", text)
    if tags_match:
        tags = [t.strip() for t in tags_match.group(1).split(",") if t.strip()]
    else:
        tags = []

    closing_match = re.search(r"洞察是[：:]\s*(.+?)(?:[。\n]|$)", text)
    title_insight = closing_match.group(1).strip() if closing_match else insight

    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title_insight)[:30].strip("-")
    filename = f"{date}-{slug}.md"

    # 构建 Obsidian tags（crucible + 语义标签）
    all_tags = ["crucible"] + [t for t in tags if t != "crucible"]
    tags_yaml = ", ".join(all_tags)

    md = f"""---
date: {date}
type: insight
source: 飞书对话
tags: [{tags_yaml}]
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
        "tags": tags,
        "title": title_insight,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_tags (
            note_id   TEXT,
            tag       TEXT,
            tag_type  TEXT,
            PRIMARY KEY (note_id, tag)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_digests (
            id          TEXT PRIMARY KEY,
            created_at  DATETIME,
            content_url TEXT,
            content_summary TEXT,
            tags        TEXT,
            status      TEXT DEFAULT 'pending',
            reminded_at DATETIME,
            digested_at DATETIME,
            note_id     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_maturity (
            tag             TEXT PRIMARY KEY,
            maturity        TEXT DEFAULT 'seed',
            conversation_count  INTEGER DEFAULT 0,
            application_count   INTEGER DEFAULT 0,
            linked_note_count   INTEGER DEFAULT 0,
            score           REAL DEFAULT 0.0,
            updated_at      DATETIME
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


def upsert_tags(conn, note_id, tags):
    """写入笔记标签。"""
    for tag in tags:
        conn.execute("""
            INSERT INTO note_tags (note_id, tag, tag_type)
            VALUES (?, ?, 'topic')
            ON CONFLICT(note_id, tag) DO NOTHING
        """, (note_id, tag))


def build_tag_index(conn):
    """从 DB 构建标签索引。"""
    rows = conn.execute("""
        SELECT nt.tag, nt.note_id, nm.created_at
        FROM note_tags nt
        LEFT JOIN note_metrics nm ON nt.note_id = nm.note_id
        ORDER BY nt.tag, nm.created_at DESC
    """).fetchall()

    tags = {}
    for tag, note_id, _ in rows:
        tags.setdefault(tag, []).append(note_id)

    # 也收集每条笔记的信息
    notes = {}
    note_rows = conn.execute("""
        SELECT nm.note_id, nm.created_at, GROUP_CONCAT(nt.tag, ', ')
        FROM note_metrics nm
        LEFT JOIN note_tags nt ON nm.note_id = nt.note_id
        GROUP BY nm.note_id
    """).fetchall()
    for note_id, created_at, tag_str in note_rows:
        notes[note_id] = {
            "created_at": created_at or "",
            "tags": [t.strip() for t in (tag_str or "").split(",") if t.strip()],
        }

    return {"tags": tags, "notes": notes}


def find_related_notes(conn, note_id, min_overlap=2, max_results=3):
    """找到标签重合最多的其他笔记。"""
    # 获取当前笔记的标签
    current_tags = [r[0] for r in conn.execute(
        "SELECT tag FROM note_tags WHERE note_id = ?", (note_id,)
    ).fetchall()]

    if not current_tags:
        return []

    placeholders = ",".join("?" * len(current_tags))
    rows = conn.execute(f"""
        SELECT note_id, COUNT(*) as overlap
        FROM note_tags
        WHERE tag IN ({placeholders}) AND note_id != ?
        GROUP BY note_id
        HAVING overlap >= ?
        ORDER BY overlap DESC
        LIMIT ?
    """, current_tags + [note_id, min_overlap, max_results]).fetchall()

    return [(note_id, overlap) for note_id, overlap in rows]


def upload_file_to_server(local_content, remote_path):
    """上传文件内容到服务器。"""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(local_content)
        tmp_path = f.name

    try:
        subprocess.run([
            "scp", "-i", SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "IdentitiesOnly=yes",
            tmp_path, f"{REMOTE_HOST}:/tmp/crucible-upload",
        ], capture_output=True, timeout=10)
        subprocess.run([
            "ssh", "-i", SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "IdentitiesOnly=yes",
            REMOTE_HOST,
            f"sudo cp /tmp/crucible-upload {remote_path}",
        ], capture_output=True, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"上传失败: {e}")
        return False
    finally:
        os.unlink(tmp_path)


def compute_maturity_level(conv_count, app_count, linked_count):
    """
    计算知识成熟度。
    基于用户行为数据：对话次数、应用场景数、关联笔记数。
    返回 {"maturity": "seed|growing|mature", "score": 0.0-1.0}
    """
    conv_score = min(conv_count / 5, 1.0)
    app_score = min(app_count / 3, 1.0)
    link_score = min(linked_count / 3, 1.0)

    score = conv_score * 0.4 + app_score * 0.35 + link_score * 0.25

    if conv_count >= 4 and app_count >= 2 and linked_count >= 2:
        maturity = "mature"
    elif conv_count >= 2 or app_count >= 1:
        maturity = "growing"
    else:
        maturity = "seed"

    return {"maturity": maturity, "score": round(score, 2)}


def update_tag_maturity(conn):
    """从 DB 数据计算每个标签的知识成熟度，写入 tag_maturity 表。"""
    rows = conn.execute("""
        SELECT
            nt.tag,
            COUNT(DISTINCT nt.note_id) as conv_count,
            COALESCE(SUM(CASE WHEN cm.has_application = 1 THEN 1 ELSE 0 END), 0) as app_count,
            COALESCE(SUM(CASE WHEN nm.linked_count > 0 THEN 1 ELSE 0 END), 0) as linked_count
        FROM note_tags nt
        LEFT JOIN note_metrics nm ON nt.note_id = nm.note_id
        LEFT JOIN conversation_metrics cm ON nt.note_id = cm.note_id
        GROUP BY nt.tag
    """).fetchall()

    now = datetime.now().isoformat()
    for tag, conv_count, app_count, linked_count in rows:
        result = compute_maturity_level(conv_count, app_count, linked_count)
        conn.execute("""
            INSERT INTO tag_maturity (tag, maturity, conversation_count, application_count, linked_note_count, score, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tag) DO UPDATE SET
                maturity = excluded.maturity,
                conversation_count = excluded.conversation_count,
                application_count = excluded.application_count,
                linked_note_count = excluded.linked_note_count,
                score = excluded.score,
                updated_at = excluded.updated_at
        """, (tag, result["maturity"], conv_count, app_count, linked_count, result["score"], now))

    conn.commit()
    logger.info(f"更新 {len(rows)} 个标签的知识成熟度")


def build_maturity_report(conn):
    """从 tag_maturity 表生成成熟度报告。"""
    rows = conn.execute("""
        SELECT tag, maturity, conversation_count, application_count, linked_note_count, score
        FROM tag_maturity
        ORDER BY score DESC
    """).fetchall()

    if not rows:
        return None

    distribution = {"seed": 0, "growing": 0, "mature": 0}
    tags = []
    for tag, maturity, conv_count, app_count, linked_count, score in rows:
        distribution[maturity] = distribution.get(maturity, 0) + 1
        tags.append({
            "tag": tag,
            "maturity": maturity,
            "conversation_count": conv_count,
            "application_count": app_count,
            "linked_note_count": linked_count,
            "score": score,
        })

    return {
        "updated_at": datetime.now().isoformat(),
        "total_tags": len(rows),
        "distribution": distribution,
        "tags": tags,
    }


def build_digest_report(conn):
    """分析待消化收藏，按标签聚类，生成推荐报告。"""
    rows = conn.execute("""
        SELECT id, created_at, content_url, content_summary, tags
        FROM pending_digests
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """).fetchall()

    if not rows:
        return None

    # 按标签聚类
    clusters = {}
    items_list = []
    for row_id, created_at, url, summary, tags_json in rows:
        tags = json.loads(tags_json) if tags_json else []
        age_days = (datetime.now() - datetime.fromisoformat(created_at)).days if created_at else 0

        item = {
            "id": row_id,
            "url": url or "",
            "summary": summary or "(无摘要)",
            "age_days": age_days,
            "tags": tags,
        }
        items_list.append(item)

        # 用第一个 tag 作为聚类 key（如果有的话）
        cluster_key = tags[0] if tags else "未分类"
        clusters.setdefault(cluster_key, []).append(item)

    # 找出和已有笔记最相关的待消化内容
    for item in items_list:
        if item["tags"]:
            placeholders = ",".join("?" * len(item["tags"]))
            related = conn.execute(f"""
                SELECT DISTINCT nt.note_id
                FROM note_tags nt
                WHERE nt.tag IN ({placeholders})
                LIMIT 3
            """, item["tags"]).fetchall()
            item["related_notes"] = [r[0] for r in related]
        else:
            item["related_notes"] = []

    # 构建报告
    report = {
        "updated_at": datetime.now().isoformat(),
        "pending_count": len(rows),
        "clusters": [],
    }

    for theme, theme_items in sorted(clusters.items(), key=lambda x: -len(x[1])):
        cluster = {
            "theme": theme,
            "count": len(theme_items),
            "items": theme_items,
        }
        # 推荐理由
        has_related = any(i["related_notes"] for i in theme_items)
        oldest = max(i["age_days"] for i in theme_items)
        if has_related:
            cluster["recommendation"] = f"和你之前的笔记有关联，建议优先消化"
        elif oldest > 7:
            cluster["recommendation"] = f"已收藏 {oldest} 天，建议尽快消化"
        else:
            cluster["recommendation"] = ""

        report["clusters"].append(cluster)

    return report


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

    # 待消化收藏
    try:
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM pending_digests WHERE status = 'pending'"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pending_count = 0

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
    if pending_count > 0:
        print(f"【待消化收藏】")
        print(f"  {pending_count} 条内容等待深入聊")
        print()

    # 生成 stats 文本用于上传到服务器
    stats_md = generate_stats_md(
        total, completed, completion_rate, has_app, app_rate,
        avg_turns, total_notes, week_total, week_completed,
        week_comp_rate, week_app, active_days, pending_count,
    )
    return stats_md


def generate_stats_md(total, completed, comp_rate, has_app, app_rate,
                      avg_turns, total_notes, week_total, week_completed,
                      week_comp_rate, week_app, active_days, pending_count=0):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    pending_line = f"\n\n【待消化收藏】{pending_count} 条" if pending_count > 0 else ""
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

【最近 30 天活跃天数】{active_days}{pending_line}
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

        logger.info("统计已上传到服务器")
    except Exception as e:
        logger.warning(f"统计上传失败（不影响本地数据）: {e}")
    finally:
        os.unlink(tmp_path)


# ── 主流程 ────────────────────────────────────────────
def sync():
    NOTE_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    synced = set(state.get("synced_msg_ids", []))

    logger.info(f"Obsidian vault: {VAULT_PATH}")
    logger.info(f"笔记目录: {NOTE_DIR}")
    logger.info(f"数据库: {DB_PATH}")
    logger.info(f"已同步笔记: {len(synced)} 条")

    token = get_token()
    logger.info("获取飞书 token 成功")

    # 拉取所有消息
    all_messages = fetch_all_messages(token)
    logger.info(f"拉取 {len(all_messages)} 条消息")

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
            logger.info(f"新增笔记: {filepath.name}")

        state["synced_msg_ids"] = list(synced)
        state["last_sync"] = datetime.now().isoformat()
        save_state(state)
        logger.info(f"新增 {len(new_notes)} 条笔记")
    else:
        logger.info("没有新笔记")

    # ── 对话分析 + 入库 ──
    logger.info("分析对话指标...")
    conversations = segment_conversations(all_messages)
    logger.info(f"识别出 {len(conversations)} 段对话")

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
    logger.info(f"写入 {conv_count} 条对话指标，{note_count} 条笔记指标")

    # ── 收藏识别 + 入库（V2 Plan 6）──
    logger.info("识别收藏内容...")
    bookmark_count = 0
    for conv_msgs in conversations:
        bot_msgs_in_conv = [m for m in conv_msgs if is_bot_msg(m)]
        user_msgs_in_conv = [m for m in conv_msgs if is_user_msg(m)]

        for bm in bot_msgs_in_conv:
            bot_text = extract_text(bm)
            if "已加入待消化" in bot_text:
                # 这是一个 bookmark 对话，找到对应的用户消息
                conv_id = conv_msgs[0].get("message_id", "")
                first_time = int(conv_msgs[0].get("create_time", "0"))
                created_at = datetime.fromtimestamp(first_time / 1000).isoformat() if first_time else ""

                # 提取用户发的 URL
                user_text = extract_text(user_msgs_in_conv[0]) if user_msgs_in_conv else ""
                url_match = re.search(r"https?://\S+", user_text)
                content_url = url_match.group(0) if url_match else ""

                # 提取 Bot 的摘要（「收到，已加入待消化。」之后的部分）
                summary_match = re.search(r"已加入待消化[。.]\s*(.+?)$", bot_text)
                content_summary = summary_match.group(1).strip() if summary_match else ""

                conn.execute("""
                    INSERT INTO pending_digests (id, created_at, content_url, content_summary, status)
                    VALUES (?, ?, ?, ?, 'pending')
                    ON CONFLICT(id) DO NOTHING
                """, (conv_id, created_at, content_url, content_summary))
                bookmark_count += 1
                break

    conn.commit()
    logger.info(f"识别 {bookmark_count} 条收藏内容")

    # ── 标签入库（V2）──
    logger.info("处理标签...")
    tag_count = 0
    for msg_id, filename, md, note_fields in new_notes:
        tags = note_fields.get("tags", [])
        if tags:
            upsert_tags(conn, msg_id, tags)
            tag_count += len(tags)
    # 也处理已有笔记（回填旧笔记的标签，从文件中读取）
    existing_notes = list(NOTE_DIR.glob("*.md"))
    for note_path in existing_notes:
        content = note_path.read_text(encoding="utf-8")
        # 从 YAML frontmatter 提取 tags
        fm_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            fm = fm_match.group(1)
            tags_line = re.search(r"tags:\s*\[(.+?)\]", fm)
            if tags_line:
                tags = [t.strip() for t in tags_line.group(1).split(",") if t.strip() and t.strip() != "crucible"]
                msg_id_match = re.search(r"feishu_msg_id:\s*(\S+)", fm)
                if msg_id_match and tags:
                    upsert_tags(conn, msg_id_match.group(1), tags)
    conn.commit()
    logger.info(f"处理 {tag_count} 个新标签")

    # ── 构建 TAG_INDEX + 上传 ──
    tag_index = build_tag_index(conn)
    tag_index_json = json.dumps(tag_index, ensure_ascii=False, indent=2)
    logger.info(f"TAG_INDEX: {len(tag_index['tags'])} 个标签，{len(tag_index['notes'])} 条笔记")

    if upload_file_to_server(tag_index_json, REMOTE_TAG_INDEX):
        logger.info("TAG_INDEX.json 已上传到服务器")

    # ── 关联笔记（双链）──
    logger.info("计算笔记关联...")
    link_updates = 0
    for note_path in NOTE_DIR.glob("*.md"):
        content = note_path.read_text(encoding="utf-8")
        fm_match = re.search(r"feishu_msg_id:\s*(\S+)", content)
        if not fm_match:
            continue
        note_id = fm_match.group(1)

        related = find_related_notes(conn, note_id)
        if not related:
            continue

        # 构建关联章节
        links = []
        for related_id, overlap in related:
            # 找到对应的文件名
            for other_path in NOTE_DIR.glob("*.md"):
                other_content = other_path.read_text(encoding="utf-8")
                if related_id in other_content:
                    link_name = other_path.stem
                    # 获取标题
                    title_match = re.search(r"^# (.+)$", other_content, re.MULTILINE)
                    title = title_match.group(1) if title_match else link_name
                    links.append(f"- [[{link_name}]] — {title}")
                    break

        if not links:
            continue

        link_section = "\n## 关联笔记\n" + "\n".join(links) + "\n"

        # 如果已有关联章节，替换；否则追加
        if "## 关联笔记" in content:
            content = re.sub(r"\n## 关联笔记\n.*?(?=\n## |\Z)", link_section, content, flags=re.DOTALL)
        else:
            content = content.rstrip() + "\n" + link_section

        note_path.write_text(content, encoding="utf-8")
        link_updates += 1

        # 更新 linked_count
        conn.execute(
            "UPDATE note_metrics SET linked_count = ? WHERE note_id = ?",
            (len(related), note_id)
        )

    conn.commit()
    logger.info(f"更新 {link_updates} 条笔记的关联")

    # ── 生成收藏消化报告（V2 Plan 7）──
    logger.info("生成收藏消化报告...")
    digest_report = build_digest_report(conn)
    if digest_report:
        report_json = json.dumps(digest_report, ensure_ascii=False, indent=2)
        logger.info(f"DIGEST_REPORT: {digest_report['pending_count']} 条待消化，{len(digest_report['clusters'])} 个主题")
        if upload_file_to_server(report_json, REMOTE_DIGEST_REPORT):
            logger.info("DIGEST_REPORT.json 已上传到服务器")
    else:
        logger.info("没有待消化收藏")

    # ── 知识成熟度计算（V3 Plan 9）──
    logger.info("计算知识成熟度...")
    update_tag_maturity(conn)
    maturity_report = build_maturity_report(conn)
    if maturity_report:
        maturity_json = json.dumps(maturity_report, ensure_ascii=False, indent=2)
        logger.info(
            f"MATURITY_REPORT: {maturity_report['total_tags']} 个标签 — "
            f"seed:{maturity_report['distribution']['seed']} "
            f"growing:{maturity_report['distribution']['growing']} "
            f"mature:{maturity_report['distribution']['mature']}"
        )
        if upload_file_to_server(maturity_json, REMOTE_MATURITY_REPORT):
            logger.info("MATURITY_REPORT.json 已上传到服务器")
    else:
        logger.info("没有标签数据，跳过成熟度计算")

    conn.close()

    # ── 上传统计到服务器 ──
    stats_md = show_stats()
    if stats_md:
        upload_stats_to_server(stats_md)


def main():
    if "--stats" in sys.argv:
        show_stats()
    elif "--pending" in sys.argv:
        show_pending()
    elif "--maturity" in sys.argv:
        show_maturity()
    else:
        sync()


def show_pending():
    """显示待消化收藏列表。"""
    if not DB_PATH.exists():
        print("还没有数据。先运行 python3 sync-notes.py 同步一次。")
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("""
            SELECT id, created_at, content_url, content_summary
            FROM pending_digests
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """).fetchall()
    except sqlite3.OperationalError:
        print("还没有收藏数据。")
        return
    finally:
        conn.close()

    if not rows:
        print("没有待消化的收藏。")
        return

    print(f"📌 待消化收藏（{len(rows)} 条）\n")
    for i, (row_id, created_at, url, summary) in enumerate(rows, 1):
        date = created_at[:10] if created_at else "?"
        print(f"  {i}. [{date}] {summary or url or '(无摘要)'}")
        if url:
            print(f"     {url}")
        print()


def show_maturity():
    """显示知识成熟度分布。"""
    if not DB_PATH.exists():
        print("还没有数据。先运行 python3 sync-notes.py 同步一次。")
        return

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT tag, maturity, conversation_count, application_count, score
        FROM tag_maturity
        ORDER BY score DESC
    """).fetchall()
    conn.close()

    if not rows:
        print("还没有成熟度数据。先同步一次。")
        return

    maturity_icons = {"seed": "🌱", "growing": "🌿", "mature": "🌳"}
    dist = {"seed": 0, "growing": 0, "mature": 0}
    for _, m, _, _, _ in rows:
        dist[m] = dist.get(m, 0) + 1

    print("=" * 40)
    print("🧠 知识成熟度")
    print("=" * 40)
    print()
    print(f"  🌱 种子：{dist['seed']}  🌿 生长中：{dist['growing']}  🌳 成熟：{dist['mature']}")
    print()
    for tag, maturity, conv, app, score in rows:
        icon = maturity_icons.get(maturity, "?")
        print(f"  {icon} {tag}（对话 {conv} 次，场景 {app} 个，分数 {score:.1f}）")
    print()


if __name__ == "__main__":
    main()
