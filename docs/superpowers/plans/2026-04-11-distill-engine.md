# Distill Engine (V3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Distill Engine — a three-layer deep knowledge processing system (Diagnose → Connect → Forge) that turns scattered atomic notes into mature, interconnected, battle-tested cognition.

**Architecture:** The Distill Engine runs as part of `sync-notes.py`, computing maturity scores, detecting themes, and preparing challenge data from existing `crucible.db` data. Results are uploaded to the server as JSON reports, which OpenClaw agents read to inform conversations. Three new user commands (`/maturity`, `/themes`, `/challenge`) let users interact with each layer.

**Tech Stack:** Python 3, SQLite (crucible.db), JSON reports, SCP upload to OpenClaw server

---

## Scope Check

V3 has three independent layers that build on each other but can each be developed and tested separately:

1. **Plan 9: Diagnose (Layer 1)** — Knowledge maturity scoring
2. **Plan 10: Connect (Layer 2)** — Fragment aggregation + theme notes
3. **Plan 11: Forge (Layer 3)** — Cognitive challenge tracking

Each plan produces working, testable software. Plan 10 depends on Plan 9's maturity data. Plan 11 depends on Plan 10's theme data.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `sync-notes.py` (modify) | New DB tables, maturity scoring, theme detection, challenge tracking, report generation, new CLI commands |
| `test_sync_notes.py` (modify) | Tests for all V3 logic (unit + integration) |
| Server: `MATURITY_REPORT.json` (new) | Per-tag maturity scores, uploaded to server for agents |
| Server: `THEME_REPORT.json` (new) | Detected themes + aggregation suggestions, uploaded to server |
| Server: `CHALLENGE_REPORT.json` (new) | Challenge candidates, uploaded to server for cognitive challenge agent |

---

## Plan 9: Diagnose — Knowledge Maturity Scoring (Layer 1)

**What it does:** Every tag in the knowledge base gets a maturity score (seed/growing/mature) based on real user behavior data — how many times a topic was discussed, whether application scenarios exist, and how well-connected the notes are.

**Why it matters:** This answers "which of my ideas are well-formed?" and later tells V4 Express "when is it time to write?"

### Task 1: New DB table `tag_maturity`

**Files:**
- Modify: `sync-notes.py:299-344` (init_db function)
- Test: `test_sync_notes.py`

- [ ] **Step 1: Write the failing test**

```python
class TestTagMaturity(unittest.TestCase):
    """测试知识成熟度"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        sn.init_db.__wrapped__(self.conn) if hasattr(sn.init_db, '__wrapped__') else None
        # Manually create all tables including new ones
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_metrics (
                id TEXT PRIMARY KEY, created_at DATETIME, input_type TEXT,
                turns INTEGER, completed BOOLEAN, has_application BOOLEAN,
                user_msg_chars INTEGER, note_id TEXT
            );
            CREATE TABLE IF NOT EXISTS note_metrics (
                note_id TEXT PRIMARY KEY, created_at DATETIME,
                linked_count INTEGER DEFAULT 0, searched_hit INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT, PRIMARY KEY (note_id, tag)
            );
            CREATE TABLE IF NOT EXISTS pending_digests (
                id TEXT PRIMARY KEY, created_at DATETIME, content_url TEXT,
                content_summary TEXT, tags TEXT, status TEXT DEFAULT 'pending',
                reminded_at DATETIME, digested_at DATETIME, note_id TEXT
            );
            CREATE TABLE IF NOT EXISTS tag_maturity (
                tag TEXT PRIMARY KEY,
                maturity TEXT DEFAULT 'seed',
                conversation_count INTEGER DEFAULT 0,
                application_count INTEGER DEFAULT 0,
                linked_note_count INTEGER DEFAULT 0,
                score REAL DEFAULT 0.0,
                updated_at DATETIME
            );
        """)

    def test_tag_maturity_table_exists(self):
        """tag_maturity 表存在且字段正确"""
        cursor = self.conn.execute("PRAGMA table_info(tag_maturity)")
        columns = {row[1] for row in cursor.fetchall()}
        self.assertIn("tag", columns)
        self.assertIn("maturity", columns)
        self.assertIn("conversation_count", columns)
        self.assertIn("application_count", columns)
        self.assertIn("linked_note_count", columns)
        self.assertIn("score", columns)
        self.assertIn("updated_at", columns)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity.test_tag_maturity_table_exists -v`
Expected: FAIL (TestTagMaturity class doesn't exist yet in test file)

- [ ] **Step 3: Add tag_maturity table to init_db**

In `sync-notes.py`, inside `init_db()`, after the `pending_digests` CREATE TABLE, add:

```python
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
```

- [ ] **Step 4: Add the test class to test_sync_notes.py and run**

Add the `TestTagMaturity` class from Step 1 to `test_sync_notes.py`. But use `sn.init_db()` properly — since init_db uses the global DB_PATH, we need to override it. Looking at existing integration tests, they create tables manually. So use the manual approach in setUp.

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity.test_tag_maturity_table_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): add tag_maturity table to crucible.db"
```

---

### Task 2: Maturity scoring function

**Files:**
- Modify: `sync-notes.py` (add `compute_tag_maturity` function)
- Test: `test_sync_notes.py`

The maturity scoring logic from the design doc:

| Maturity | Criteria | Score range |
|----------|----------|-------------|
| seed | conversation_count = 1, application_count = 0 | 0.0 - 0.3 |
| growing | conversation_count 2-3, OR application_count >= 1 | 0.3 - 0.7 |
| mature | conversation_count >= 4, application_count >= 2, linked_note_count >= 2 | 0.7 - 1.0 |

- [ ] **Step 1: Write the failing tests**

```python
    def test_maturity_seed(self):
        """只聊过 1 次、没有应用场景 → seed"""
        result = sn.compute_maturity_level(conv_count=1, app_count=0, linked_count=0)
        self.assertEqual(result["maturity"], "seed")
        self.assertLessEqual(result["score"], 0.3)

    def test_maturity_growing_by_conversations(self):
        """聊过 2-3 次 → growing"""
        result = sn.compute_maturity_level(conv_count=2, app_count=0, linked_count=0)
        self.assertEqual(result["maturity"], "growing")
        self.assertGreater(result["score"], 0.3)
        self.assertLessEqual(result["score"], 0.7)

    def test_maturity_growing_by_application(self):
        """有 1 个应用场景 → growing"""
        result = sn.compute_maturity_level(conv_count=1, app_count=1, linked_count=0)
        self.assertEqual(result["maturity"], "growing")

    def test_maturity_mature(self):
        """多次追问 + 多个场景 + 多关联 → mature"""
        result = sn.compute_maturity_level(conv_count=5, app_count=3, linked_count=3)
        self.assertEqual(result["maturity"], "mature")
        self.assertGreater(result["score"], 0.7)

    def test_maturity_edge_not_mature_without_links(self):
        """聊了很多次但没有关联笔记 → 不到 mature"""
        result = sn.compute_maturity_level(conv_count=5, app_count=2, linked_count=0)
        self.assertNotEqual(result["maturity"], "mature")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity -v`
Expected: FAIL with "module 'sync_notes' has no attribute 'compute_maturity_level'"

- [ ] **Step 3: Implement compute_maturity_level**

Add to `sync-notes.py`:

```python
def compute_maturity_level(conv_count, app_count, linked_count):
    """
    计算知识成熟度。
    基于用户行为数据：对话次数、应用场景数、关联笔记数。
    返回 {"maturity": "seed|growing|mature", "score": 0.0-1.0}
    """
    # 加权评分：对话次数(40%) + 应用场景(35%) + 关联笔记(25%)
    conv_score = min(conv_count / 5, 1.0)    # 5次对话满分
    app_score = min(app_count / 3, 1.0)      # 3个场景满分
    link_score = min(linked_count / 3, 1.0)  # 3个关联满分

    score = conv_score * 0.4 + app_score * 0.35 + link_score * 0.25

    # 判断等级
    if conv_count >= 4 and app_count >= 2 and linked_count >= 2:
        maturity = "mature"
    elif conv_count >= 2 or app_count >= 1:
        maturity = "growing"
    else:
        maturity = "seed"

    return {"maturity": maturity, "score": round(score, 2)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): add maturity scoring function — seed/growing/mature"
```

---

### Task 3: Compute maturity for all tags from DB

**Files:**
- Modify: `sync-notes.py` (add `update_tag_maturity` function)
- Test: `test_sync_notes.py`

This function queries the DB to count conversations, applications, and links per tag, then scores each tag.

- [ ] **Step 1: Write the failing test**

```python
    def test_update_tag_maturity_from_db(self):
        """从 DB 数据计算每个标签的成熟度"""
        # 准备数据：标签 "远程办公" 出现在 3 条笔记中，其中 2 条有应用场景
        for i in range(3):
            note_id = f"note_{i}"
            self.conn.execute(
                "INSERT INTO note_metrics (note_id, created_at, linked_count) VALUES (?, ?, ?)",
                (note_id, "2026-04-01", 1 if i < 2 else 0)
            )
            self.conn.execute(
                "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, ?, 'topic')",
                (note_id, "远程办公")
            )
            self.conn.execute(
                "INSERT INTO conversation_metrics (id, created_at, input_type, turns, completed, has_application, user_msg_chars, note_id) VALUES (?, ?, 'thought', 3, 1, ?, 100, ?)",
                (f"conv_{i}", "2026-04-01", 1 if i < 2 else 0, note_id)
            )
        self.conn.commit()

        sn.update_tag_maturity(self.conn)

        row = self.conn.execute(
            "SELECT maturity, conversation_count, application_count FROM tag_maturity WHERE tag = ?",
            ("远程办公",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "growing")  # 3 conversations, 2 applications
        self.assertEqual(row[1], 3)
        self.assertEqual(row[2], 2)

    def test_update_tag_maturity_seed(self):
        """只有 1 条笔记的标签 → seed"""
        self.conn.execute(
            "INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')"
        )
        self.conn.execute(
            "INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '新话题', 'topic')"
        )
        self.conn.execute(
            "INSERT INTO conversation_metrics (id, created_at, input_type, turns, completed, has_application, user_msg_chars, note_id) VALUES ('c1', '2026-04-01', 'thought', 2, 1, 0, 50, 'n1')"
        )
        self.conn.commit()

        sn.update_tag_maturity(self.conn)

        row = self.conn.execute(
            "SELECT maturity FROM tag_maturity WHERE tag = ?", ("新话题",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "seed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity.test_update_tag_maturity_from_db test_sync_notes.TestTagMaturity.test_update_tag_maturity_seed -v`
Expected: FAIL with "module 'sync_notes' has no attribute 'update_tag_maturity'"

- [ ] **Step 3: Implement update_tag_maturity**

Add to `sync-notes.py`:

```python
def update_tag_maturity(conn):
    """从 DB 数据计算每个标签的知识成熟度，写入 tag_maturity 表。"""
    # 查询每个标签的统计数据
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): compute tag maturity from DB — conversation/application/link counts"
```

---

### Task 4: MATURITY_REPORT.json generation + upload

**Files:**
- Modify: `sync-notes.py` (add `build_maturity_report`)
- Test: `test_sync_notes.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_build_maturity_report(self):
        """生成成熟度报告 JSON"""
        # 插入 tag_maturity 数据
        now = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, conversation_count, application_count, linked_note_count, score, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("远程办公", "growing", 3, 2, 1, 0.52, now)
        )
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, conversation_count, application_count, linked_note_count, score, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("AI产品", "seed", 1, 0, 0, 0.08, now)
        )
        self.conn.commit()

        report = sn.build_maturity_report(self.conn)
        self.assertIsNotNone(report)
        self.assertEqual(report["total_tags"], 2)
        self.assertEqual(report["distribution"]["seed"], 1)
        self.assertEqual(report["distribution"]["growing"], 1)
        self.assertEqual(report["distribution"]["mature"], 0)
        self.assertEqual(len(report["tags"]), 2)
        # 按 score 降序
        self.assertEqual(report["tags"][0]["tag"], "远程办公")

    def test_build_maturity_report_empty(self):
        """没有标签时返回 None"""
        report = sn.build_maturity_report(self.conn)
        self.assertIsNone(report)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity.test_build_maturity_report test_sync_notes.TestTagMaturity.test_build_maturity_report_empty -v`
Expected: FAIL

- [ ] **Step 3: Implement build_maturity_report**

Add to `sync-notes.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestTagMaturity -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): generate MATURITY_REPORT.json from tag maturity data"
```

---

### Task 5: Wire maturity into sync() + add /maturity CLI command

**Files:**
- Modify: `sync-notes.py:691-907` (sync function) and `main` function
- Test: manual verification

- [ ] **Step 1: Add maturity computation to sync()**

In `sync-notes.py`, inside the `sync()` function, after the digest report section (around line 900), add:

```python
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
```

- [ ] **Step 2: Add REMOTE_MATURITY_REPORT constant**

Near the other REMOTE_ constants (around line 53):

```python
REMOTE_MATURITY_REPORT = "/root/.openclaw/workspace/MATURITY_REPORT.json"
```

- [ ] **Step 3: Add show_maturity() function and /maturity command**

```python
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
```

- [ ] **Step 4: Update main() to support --maturity flag**

```python
def main():
    if "--stats" in sys.argv:
        show_stats()
    elif "--pending" in sys.argv:
        show_pending()
    elif "--maturity" in sys.argv:
        show_maturity()
    else:
        sync()
```

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py
git commit -m "feat(v3): wire maturity scoring into sync + add --maturity CLI command"
```

---

## Plan 10: Connect — Fragment Aggregation (Layer 2)

**What it does:** Finds hidden themes across notes — when 3+ notes share a tag or similar viewpoints, suggest aggregating them into a higher-level "theme note."

**Why it matters:** Users don't realize that 4 separate conversations are all about the same underlying idea. This layer surfaces those patterns.

### Task 6: Theme detection function

**Files:**
- Modify: `sync-notes.py` (add `detect_themes`)
- Test: `test_sync_notes.py`

A "theme" is a tag that has 3+ notes AND at least 1 note is "growing" or above.

- [ ] **Step 1: Write the failing tests**

```python
class TestThemeDetection(unittest.TestCase):
    """测试碎片聚合（主题发现）"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS note_metrics (
                note_id TEXT PRIMARY KEY, created_at DATETIME,
                linked_count INTEGER DEFAULT 0, searched_hit INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT, PRIMARY KEY (note_id, tag)
            );
            CREATE TABLE IF NOT EXISTS tag_maturity (
                tag TEXT PRIMARY KEY, maturity TEXT DEFAULT 'seed',
                conversation_count INTEGER DEFAULT 0, application_count INTEGER DEFAULT 0,
                linked_note_count INTEGER DEFAULT 0, score REAL DEFAULT 0.0, updated_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS conversation_metrics (
                id TEXT PRIMARY KEY, created_at DATETIME, input_type TEXT,
                turns INTEGER, completed BOOLEAN, has_application BOOLEAN,
                user_msg_chars INTEGER, note_id TEXT
            );
        """)

    def test_detect_theme_with_3_notes(self):
        """标签下有 3+ 条笔记 → 检测为主题"""
        for i in range(3):
            self.conn.execute(
                "INSERT INTO note_metrics (note_id, created_at) VALUES (?, '2026-04-01')",
                (f"n{i}",)
            )
            self.conn.execute(
                "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, '知行合一', 'topic')",
                (f"n{i}",)
            )
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, score) VALUES ('知行合一', 'growing', 0.5)"
        )
        self.conn.commit()

        themes = sn.detect_themes(self.conn)
        self.assertEqual(len(themes), 1)
        self.assertEqual(themes[0]["tag"], "知行合一")
        self.assertEqual(themes[0]["note_count"], 3)

    def test_no_theme_with_2_notes(self):
        """标签下只有 2 条笔记 → 不算主题"""
        for i in range(2):
            self.conn.execute(
                "INSERT INTO note_metrics (note_id, created_at) VALUES (?, '2026-04-01')",
                (f"n{i}",)
            )
            self.conn.execute(
                "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, '新话题', 'topic')",
                (f"n{i}",)
            )
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, score) VALUES ('新话题', 'seed', 0.1)"
        )
        self.conn.commit()

        themes = sn.detect_themes(self.conn)
        self.assertEqual(len(themes), 0)

    def test_themes_sorted_by_score(self):
        """主题按成熟度 score 降序排列"""
        for tag, score, maturity in [("AI产品", 0.6, "growing"), ("远程办公", 0.8, "mature")]:
            for i in range(3):
                nid = f"{tag}_{i}"
                self.conn.execute(
                    "INSERT INTO note_metrics (note_id, created_at) VALUES (?, '2026-04-01')", (nid,)
                )
                self.conn.execute(
                    "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, ?, 'topic')", (nid, tag)
                )
            self.conn.execute(
                "INSERT INTO tag_maturity (tag, maturity, score) VALUES (?, ?, ?)",
                (tag, maturity, score)
            )
        self.conn.commit()

        themes = sn.detect_themes(self.conn)
        self.assertEqual(len(themes), 2)
        self.assertEqual(themes[0]["tag"], "远程办公")  # higher score first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestThemeDetection -v`
Expected: FAIL

- [ ] **Step 3: Implement detect_themes**

Add to `sync-notes.py`:

```python
def detect_themes(conn, min_notes=3):
    """
    检测可聚合的主题。
    条件：某个标签下有 min_notes 条以上笔记。
    返回按 score 降序排列的主题列表。
    """
    rows = conn.execute("""
        SELECT nt.tag, COUNT(DISTINCT nt.note_id) as note_count,
               tm.maturity, tm.score
        FROM note_tags nt
        JOIN tag_maturity tm ON nt.tag = tm.tag
        GROUP BY nt.tag
        HAVING note_count >= ?
        ORDER BY tm.score DESC
    """, (min_notes,)).fetchall()

    themes = []
    for tag, note_count, maturity, score in rows:
        # 获取该主题下的所有笔记 ID
        note_ids = [r[0] for r in conn.execute(
            "SELECT note_id FROM note_tags WHERE tag = ?", (tag,)
        ).fetchall()]

        themes.append({
            "tag": tag,
            "note_count": note_count,
            "maturity": maturity,
            "score": score,
            "note_ids": note_ids,
        })

    return themes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestThemeDetection -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): detect themes from tags with 3+ notes"
```

---

### Task 7: THEME_REPORT.json generation

**Files:**
- Modify: `sync-notes.py` (add `build_theme_report`)
- Test: `test_sync_notes.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_build_theme_report(self):
        """生成主题报告"""
        for i in range(4):
            nid = f"n{i}"
            self.conn.execute(
                "INSERT INTO note_metrics (note_id, created_at) VALUES (?, '2026-04-01')", (nid,)
            )
            self.conn.execute(
                "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, '知行合一', 'topic')", (nid,)
            )
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, conversation_count, score) VALUES ('知行合一', 'growing', 4, 0.55)"
        )
        self.conn.commit()

        report = sn.build_theme_report(self.conn)
        self.assertIsNotNone(report)
        self.assertEqual(report["theme_count"], 1)
        self.assertEqual(report["themes"][0]["tag"], "知行合一")
        self.assertIn("suggestion", report["themes"][0])

    def test_build_theme_report_empty(self):
        """没有主题时返回 None"""
        report = sn.build_theme_report(self.conn)
        self.assertIsNone(report)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestThemeDetection.test_build_theme_report test_sync_notes.TestThemeDetection.test_build_theme_report_empty -v`
Expected: FAIL

- [ ] **Step 3: Implement build_theme_report**

Add to `sync-notes.py`:

```python
def build_theme_report(conn):
    """生成主题聚合报告。"""
    themes = detect_themes(conn)
    if not themes:
        return None

    report_themes = []
    for theme in themes:
        suggestion = (
            f"你在「{theme['tag']}」这个方向已经聊了 {theme['note_count']} 次，"
            f"目前处于{'🌱 种子' if theme['maturity'] == 'seed' else '🌿 生长中' if theme['maturity'] == 'growing' else '🌳 成熟'}阶段。"
            f"要不要把这些想法整合成一个完整观点？"
        )
        report_themes.append({
            "tag": theme["tag"],
            "note_count": theme["note_count"],
            "maturity": theme["maturity"],
            "score": theme["score"],
            "note_ids": theme["note_ids"],
            "suggestion": suggestion,
        })

    return {
        "updated_at": datetime.now().isoformat(),
        "theme_count": len(report_themes),
        "themes": report_themes,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestThemeDetection -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): generate THEME_REPORT.json with aggregation suggestions"
```

---

### Task 8: Wire themes into sync() + add /themes CLI command

**Files:**
- Modify: `sync-notes.py`

- [ ] **Step 1: Add theme detection to sync()**

After the maturity report section in `sync()`:

```python
    # ── 主题聚合检测（V3 Plan 10）──
    logger.info("检测可聚合主题...")
    theme_report = build_theme_report(conn)
    if theme_report:
        theme_json = json.dumps(theme_report, ensure_ascii=False, indent=2)
        logger.info(f"THEME_REPORT: {theme_report['theme_count']} 个可聚合主题")
        if upload_file_to_server(theme_json, REMOTE_THEME_REPORT):
            logger.info("THEME_REPORT.json 已上传到服务器")
    else:
        logger.info("暂无可聚合主题")
```

- [ ] **Step 2: Add REMOTE_THEME_REPORT constant**

```python
REMOTE_THEME_REPORT = "/root/.openclaw/workspace/THEME_REPORT.json"
```

- [ ] **Step 3: Add show_themes() function**

```python
def show_themes():
    """显示可聚合的主题。"""
    if not DB_PATH.exists():
        print("还没有数据。先运行 python3 sync-notes.py 同步一次。")
        return

    conn = sqlite3.connect(str(DB_PATH))
    themes = detect_themes(conn)
    conn.close()

    if not themes:
        print("暂无可聚合的主题（需要某个标签下有 3+ 条笔记）。")
        return

    maturity_icons = {"seed": "🌱", "growing": "🌿", "mature": "🌳"}
    print("=" * 40)
    print("🔗 可聚合主题")
    print("=" * 40)
    print()
    for i, theme in enumerate(themes, 1):
        icon = maturity_icons.get(theme["maturity"], "?")
        print(f"  {i}. {icon} {theme['tag']}（{theme['note_count']} 条笔记，分数 {theme['score']:.1f}）")
        print(f"     → 这些笔记可能在讲同一个底层观点，值得整合")
    print()
```

- [ ] **Step 4: Update main()**

```python
def main():
    if "--stats" in sys.argv:
        show_stats()
    elif "--pending" in sys.argv:
        show_pending()
    elif "--maturity" in sys.argv:
        show_maturity()
    elif "--themes" in sys.argv:
        show_themes()
    else:
        sync()
```

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py
git commit -m "feat(v3): wire theme detection into sync + add --themes CLI command"
```

---

## Plan 11: Forge — Cognitive Challenge Tracking (Layer 3)

**What it does:** Tracks which ideas have been "challenged" (tested against counterexamples and reverse questioning), and identifies candidates for cognitive forging.

**Why it matters:** This is Crucible's most differentiated capability — instead of agreeing with the user, it challenges their thinking to make ideas more robust.

**Design constraint:** The actual challenge conversation happens on the server side (Socratic Agent). The client side only tracks which notes have been challenged and identifies challenge candidates.

### Task 9: New DB table `challenge_log` + challenge candidate detection

**Files:**
- Modify: `sync-notes.py`
- Test: `test_sync_notes.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestCognitiveChallenge(unittest.TestCase):
    """测试认知挑战（锻造）"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS note_metrics (
                note_id TEXT PRIMARY KEY, created_at DATETIME,
                linked_count INTEGER DEFAULT 0, searched_hit INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT, PRIMARY KEY (note_id, tag)
            );
            CREATE TABLE IF NOT EXISTS tag_maturity (
                tag TEXT PRIMARY KEY, maturity TEXT DEFAULT 'seed',
                conversation_count INTEGER DEFAULT 0, application_count INTEGER DEFAULT 0,
                linked_note_count INTEGER DEFAULT 0, score REAL DEFAULT 0.0, updated_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS challenge_log (
                note_id TEXT PRIMARY KEY,
                challenge_type TEXT,
                challenged_at DATETIME,
                outcome TEXT
            );
        """)

    def test_challenge_log_table_exists(self):
        """challenge_log 表存在"""
        cursor = self.conn.execute("PRAGMA table_info(challenge_log)")
        columns = {row[1] for row in cursor.fetchall()}
        self.assertIn("note_id", columns)
        self.assertIn("challenge_type", columns)
        self.assertIn("challenged_at", columns)
        self.assertIn("outcome", columns)

    def test_find_challenge_candidates_growing(self):
        """生长中的标签下的笔记 → 可被挑战"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, score) VALUES ('远程办公', 'growing', 0.5)"
        )
        self.conn.execute(
            "INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')"
        )
        self.conn.execute(
            "INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '远程办公', 'topic')"
        )
        self.conn.commit()

        candidates = sn.find_challenge_candidates(self.conn)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["note_id"], "n1")

    def test_no_challenge_for_seed(self):
        """种子阶段的笔记 → 不挑战（鼓励为主）"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, score) VALUES ('新话题', 'seed', 0.1)"
        )
        self.conn.execute(
            "INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')"
        )
        self.conn.execute(
            "INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '新话题', 'topic')"
        )
        self.conn.commit()

        candidates = sn.find_challenge_candidates(self.conn)
        self.assertEqual(len(candidates), 0)

    def test_no_challenge_for_already_challenged(self):
        """已经被挑战过的笔记 → 不重复挑战"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, score) VALUES ('远程办公', 'growing', 0.5)"
        )
        self.conn.execute(
            "INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')"
        )
        self.conn.execute(
            "INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '远程办公', 'topic')"
        )
        self.conn.execute(
            "INSERT INTO challenge_log (note_id, challenge_type, challenged_at, outcome) VALUES ('n1', 'counterexample', '2026-04-05', 'strengthened')"
        )
        self.conn.commit()

        candidates = sn.find_challenge_candidates(self.conn)
        self.assertEqual(len(candidates), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestCognitiveChallenge -v`
Expected: FAIL

- [ ] **Step 3: Add challenge_log table to init_db + implement find_challenge_candidates**

In `sync-notes.py`, add to `init_db()`:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS challenge_log (
            note_id         TEXT PRIMARY KEY,
            challenge_type  TEXT,
            challenged_at   DATETIME,
            outcome         TEXT
        )
    """)
```

Add function:

```python
def find_challenge_candidates(conn):
    """
    找到可以被认知挑战的笔记。
    条件：所属标签成熟度 ≥ growing，且未被挑战过。
    设计原则：种子阶段以鼓励为主，不挑战。
    """
    rows = conn.execute("""
        SELECT DISTINCT nt.note_id, nt.tag, tm.maturity, tm.score
        FROM note_tags nt
        JOIN tag_maturity tm ON nt.tag = tm.tag
        LEFT JOIN challenge_log cl ON nt.note_id = cl.note_id
        WHERE tm.maturity IN ('growing', 'mature')
          AND cl.note_id IS NULL
        ORDER BY tm.score DESC
    """).fetchall()

    candidates = []
    seen_notes = set()
    for note_id, tag, maturity, score in rows:
        if note_id in seen_notes:
            continue
        seen_notes.add(note_id)
        candidates.append({
            "note_id": note_id,
            "tag": tag,
            "maturity": maturity,
            "score": score,
        })

    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestCognitiveChallenge -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): challenge_log table + find challenge candidates (growing+ only)"
```

---

### Task 10: CHALLENGE_REPORT.json + wire into sync() + /challenge CLI

**Files:**
- Modify: `sync-notes.py`
- Test: `test_sync_notes.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_build_challenge_report(self):
        """生成挑战候选报告"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, score) VALUES ('远程办公', 'growing', 0.5)"
        )
        self.conn.execute(
            "INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')"
        )
        self.conn.execute(
            "INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '远程办公', 'topic')"
        )
        self.conn.commit()

        report = sn.build_challenge_report(self.conn)
        self.assertIsNotNone(report)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["note_id"], "n1")

    def test_build_challenge_report_empty(self):
        """没有候选时返回 None"""
        report = sn.build_challenge_report(self.conn)
        self.assertIsNone(report)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes.TestCognitiveChallenge.test_build_challenge_report test_sync_notes.TestCognitiveChallenge.test_build_challenge_report_empty -v`
Expected: FAIL

- [ ] **Step 3: Implement build_challenge_report + show_challenges + wire into sync/main**

```python
REMOTE_CHALLENGE_REPORT = "/root/.openclaw/workspace/CHALLENGE_REPORT.json"


def build_challenge_report(conn):
    """生成认知挑战候选报告。"""
    candidates = find_challenge_candidates(conn)
    if not candidates:
        return None

    return {
        "updated_at": datetime.now().isoformat(),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def show_challenges():
    """显示可被认知挑战的笔记。"""
    if not DB_PATH.exists():
        print("还没有数据。先运行 python3 sync-notes.py 同步一次。")
        return

    conn = sqlite3.connect(str(DB_PATH))
    candidates = find_challenge_candidates(conn)

    # 也显示已挑战的
    challenged = conn.execute("""
        SELECT note_id, challenge_type, challenged_at, outcome
        FROM challenge_log ORDER BY challenged_at DESC
    """).fetchall()
    conn.close()

    print("=" * 40)
    print("⚔️ 认知挑战")
    print("=" * 40)
    print()

    if candidates:
        print(f"【待挑战】{len(candidates)} 条想法可以被锻造：")
        for i, c in enumerate(candidates, 1):
            maturity_icon = "🌿" if c["maturity"] == "growing" else "🌳"
            print(f"  {i}. {maturity_icon} [{c['tag']}] {c['note_id'][:20]}...")
        print()

    if challenged:
        print(f"【已锻造】{len(challenged)} 条想法经过了挑战：")
        for note_id, ctype, cat, outcome in challenged:
            outcome_icon = "✅" if outcome == "strengthened" else "🔄"
            print(f"  {outcome_icon} {note_id[:20]}... ({ctype}, {outcome})")
        print()

    if not candidates and not challenged:
        print("  暂无可挑战的想法（需要标签成熟度达到「生长中」以上）")
        print()
```

- [ ] **Step 4: Wire into sync() and main()**

In `sync()`, after theme report section:

```python
    # ── 认知挑战候选（V3 Plan 11）──
    logger.info("识别认知挑战候选...")
    challenge_report = build_challenge_report(conn)
    if challenge_report:
        challenge_json = json.dumps(challenge_report, ensure_ascii=False, indent=2)
        logger.info(f"CHALLENGE_REPORT: {challenge_report['candidate_count']} 条待挑战")
        if upload_file_to_server(challenge_json, REMOTE_CHALLENGE_REPORT):
            logger.info("CHALLENGE_REPORT.json 已上传到服务器")
    else:
        logger.info("暂无挑战候选")
```

In `main()`:

```python
def main():
    if "--stats" in sys.argv:
        show_stats()
    elif "--pending" in sys.argv:
        show_pending()
    elif "--maturity" in sys.argv:
        show_maturity()
    elif "--themes" in sys.argv:
        show_themes()
    elif "--challenges" in sys.argv:
        show_challenges()
    else:
        sync()
```

- [ ] **Step 5: Run all tests**

Run: `cd /Users/tracy_t/Desktop/crucible-v3-distill && python3 -m unittest test_sync_notes -v`
Expected: All tests PASS (V1/V2 existing 45 + V3 new ~15 tests)

- [ ] **Step 6: Commit**

```bash
cd /Users/tracy_t/Desktop/crucible-v3-distill
git add sync-notes.py test_sync_notes.py
git commit -m "feat(v3): challenge report + --challenges CLI + wire all V3 into sync"
```

---

## Summary: What V3 Adds

### New DB Tables
| Table | Purpose |
|-------|---------|
| `tag_maturity` | Per-tag maturity scores (seed/growing/mature) |
| `challenge_log` | Tracks which notes have been cognitively challenged |

### New Server Reports
| File | Purpose |
|------|---------|
| `MATURITY_REPORT.json` | Maturity distribution for agents to reference |
| `THEME_REPORT.json` | Detected themes with aggregation suggestions |
| `CHALLENGE_REPORT.json` | Challenge candidate list for cognitive challenge agent |

### New CLI Commands
| Command | What it shows |
|---------|--------------|
| `--maturity` | Knowledge maturity distribution (🌱/🌿/🌳 per tag) |
| `--themes` | Aggregatable themes (3+ notes sharing a tag) |
| `--challenges` | Challenge candidates + already-challenged notes |

### New Functions (sync-notes.py)
| Function | Layer |
|----------|-------|
| `compute_maturity_level()` | L1 Diagnose |
| `update_tag_maturity()` | L1 Diagnose |
| `build_maturity_report()` | L1 Diagnose |
| `show_maturity()` | L1 Diagnose |
| `detect_themes()` | L2 Connect |
| `build_theme_report()` | L2 Connect |
| `show_themes()` | L2 Connect |
| `find_challenge_candidates()` | L3 Forge |
| `build_challenge_report()` | L3 Forge |
| `show_challenges()` | L3 Forge |

### New Tests (~15 tests)
| Class | Tests |
|-------|-------|
| `TestTagMaturity` | 7 tests (table, scoring, DB computation, report) |
| `TestThemeDetection` | 4 tests (detection, sorting, report) |
| `TestCognitiveChallenge` | 4 tests (candidates, exclusions, report) |

### V3 Evaluation Metrics (from design doc)
| Metric | Source |
|--------|--------|
| Maturity distribution (seed/growing/mature %) | `tag_maturity` table |
| Theme notes generated | `THEME_REPORT.json` theme_count |
| Challenge acceptance rate | `challenge_log` outcome counts |

---

## What V3 Does NOT Include (Server-Side Agent Changes)

The following are **server-side OpenClaw agent changes** that pair with this client-side work. They are documented here for completeness but are a separate scope:

1. **SOUL.md updates**: Agent reads MATURITY_REPORT to inform conversations ("你这个想法还在种子阶段，要不要再聊聊？")
2. **Challenge Agent behavior**: When Socratic Agent detects a "growing" topic, it switches to challenge mode (counterexamples, reverse questioning)
3. **Theme aggregation conversation**: Agent reads THEME_REPORT and initiates aggregation dialogue ("你最近聊了 4 个话题都在讲同一件事...")
4. **HEARTBEAT.md new tasks**: Weekly maturity summary + challenge suggestions

These can be planned as a separate "Plan 12: Server-Side Distill Integration" after the client-side V3 code is verified.
