#!/usr/bin/env python3
"""
Crucible 笔记同步脚本
从飞书聊天消息中提取 Crucible 笔记，保存到 Obsidian vault。

用法：
    python3 sync-notes.py
    # 或指定 vault 路径：
    OBSIDIAN_VAULT_PATH=/path/to/vault python3 sync-notes.py
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime
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


# ── 消息解析 ──────────────────────────────────────────
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


def parse_note(text, msg_id, create_time_ms):
    """从纯文本中解析出笔记的各个字段。"""
    # 日期
    date_match = re.search(r"日期[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

    # 洞察
    insight_match = re.search(r"洞察[：:]\s*(.+?)(?:\n|$)", text)
    insight = insight_match.group(1).strip() if insight_match else "未提取到洞察"

    # 原始触发
    trigger_match = re.search(r"原始触发\s*\n(.+?)(?:\n|$)", text)
    trigger = trigger_match.group(1).strip() if trigger_match else ""

    # 追问摘要
    summary_match = re.search(r"追问摘要\s*\n(.+?)(?:\n|$)", text)
    summary = summary_match.group(1).strip() if summary_match else ""

    # 个人应用场景
    scene_match = re.search(r"个人应用场景\s*\n(.+?)(?:\n|$)", text)
    scene = scene_match.group(1).strip() if scene_match else "待补充"

    # 收尾句（"✅ 这次追问到这里" 之后的洞察句）
    closing_match = re.search(r"洞察是[：:]\s*(.+?)(?:[。\n]|$)", text)
    title_insight = closing_match.group(1).strip() if closing_match else insight

    # 文件名：用洞察的前几个关键词
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title_insight)[:30].strip("-")
    filename = f"{date}-{slug}.md"

    # 生成 Obsidian markdown
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
    return filename, md


# ── 同步状态 ──────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"synced_msg_ids": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── 主流程 ────────────────────────────────────────────
def main():
    NOTE_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    synced = set(state.get("synced_msg_ids", []))

    print(f"Obsidian vault: {VAULT_PATH}")
    print(f"笔记目录: {NOTE_DIR}")
    print(f"已同步: {len(synced)} 条\n")

    token = get_token()
    print("✓ 获取飞书 token 成功")

    # 拉取消息，最多翻 3 页（150 条）
    new_notes = []
    page_token = None
    for page in range(3):
        result = list_messages(token, page_token)
        items = result.get("data", {}).get("items", [])
        if not items:
            break

        for item in items:
            msg_id = item.get("message_id", "")
            if msg_id in synced:
                continue
            if item.get("sender", {}).get("sender_type") != "app":
                continue
            if item.get("msg_type") != "post":
                continue

            raw = item.get("body", {}).get("content", "")
            text = extract_text_from_post(raw)

            if not is_crucible_note(text):
                continue

            create_time = item.get("create_time", "0")
            filename, md = parse_note(text, msg_id, create_time)
            new_notes.append((msg_id, filename, md))

        if not result.get("data", {}).get("has_more"):
            break
        page_token = result.get("data", {}).get("page_token")

    if not new_notes:
        print("\n没有新的 Crucible 笔记。")
        return

    # 写入文件
    for msg_id, filename, md in new_notes:
        filepath = NOTE_DIR / filename
        # 避免覆盖：同名文件加序号
        if filepath.exists():
            base = filepath.stem
            for i in range(2, 100):
                filepath = NOTE_DIR / f"{base}-{i}.md"
                if not filepath.exists():
                    break

        filepath.write_text(md, encoding="utf-8")
        synced.add(msg_id)
        print(f"  📝 {filepath.name}")

    # 保存状态
    state["synced_msg_ids"] = list(synced)
    state["last_sync"] = datetime.now().isoformat()
    save_state(state)

    print(f"\n✅ 同步完成，新增 {len(new_notes)} 条笔记到 {NOTE_DIR}")


if __name__ == "__main__":
    main()
