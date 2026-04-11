#!/usr/bin/env python3
"""
Crucible sync-notes.py 测试

单元测试：测试核心解析、分段、分析函数（不依赖网络和数据库）
集成测试：测试数据库读写 + 标签关联 + 收藏消化报告（用内存 SQLite）
"""

import importlib.util
import json
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# 加载 sync-notes.py（文件名有连字符，不能直接 import）
spec = importlib.util.spec_from_file_location(
    "sync_notes", Path(__file__).parent / "sync-notes.py"
)
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)


# ════════════════════════════════════════════════════════
# 单元测试
# ════════════════════════════════════════════════════════


class TestExtractText(unittest.TestCase):
    """测试消息文本提取"""

    def test_text_message(self):
        item = {"msg_type": "text", "body": {"content": '{"text": "你好世界"}'}}
        self.assertEqual(sn.extract_text(item), "你好世界")

    def test_text_message_empty(self):
        item = {"msg_type": "text", "body": {"content": '{"text": ""}'}}
        self.assertEqual(sn.extract_text(item), "")

    def test_invalid_json(self):
        item = {"msg_type": "text", "body": {"content": "not json"}}
        self.assertEqual(sn.extract_text(item), "")

    def test_image_message_returns_empty(self):
        item = {"msg_type": "image", "body": {"content": "{}"}}
        self.assertEqual(sn.extract_text(item), "")


class TestExtractTextFromPost(unittest.TestCase):
    """测试富文本消息解析"""

    def test_simple_post(self):
        post = {
            "content": [
                [{"tag": "text", "text": "第一行"}],
                [{"tag": "text", "text": "第二行"}],
            ]
        }
        result = sn.extract_text_from_post(json.dumps(post))
        self.assertIn("第一行", result)
        self.assertIn("第二行", result)

    def test_post_with_hr(self):
        post = {"content": [[{"tag": "hr"}]]}
        result = sn.extract_text_from_post(json.dumps(post))
        self.assertIn("---", result)

    def test_invalid_post(self):
        self.assertEqual(sn.extract_text_from_post("not json"), "")


class TestIsCrucibleNote(unittest.TestCase):
    """测试笔记识别"""

    def test_is_note(self):
        self.assertTrue(sn.is_crucible_note("这是一篇 Crucible 笔记"))

    def test_not_note(self):
        self.assertFalse(sn.is_crucible_note("普通对话消息"))


class TestIsUserBotMsg(unittest.TestCase):
    """测试用户/Bot消息判断"""

    def test_user_msg(self):
        item = {"sender": {"sender_type": "user"}}
        self.assertTrue(sn.is_user_msg(item))
        self.assertFalse(sn.is_bot_msg(item))

    def test_bot_msg(self):
        item = {"sender": {"sender_type": "app"}}
        self.assertTrue(sn.is_bot_msg(item))
        self.assertFalse(sn.is_user_msg(item))


class TestParseNote(unittest.TestCase):
    """测试笔记解析——对应测评方案用例 1 的笔记格式检查"""

    def setUp(self):
        self.note_text = """Crucible 笔记
日期：2026-04-10
洞察：收藏行为本质是对信息焦虑的缓解
原始触发
我觉得很多人学东西只停留在收藏
追问摘要
通过追问发现收藏是一种心理安慰机制
个人应用场景
下次看到想收藏的文章先问自己三个问题
标签：知识管理, 信息焦虑, 学习方法"""

    def test_parse_date(self):
        filename, md, fields = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertEqual(fields["date"], "2026-04-10")

    def test_parse_insight(self):
        _, _, fields = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertIn("收藏行为", fields["insight"])

    def test_parse_trigger(self):
        _, _, fields = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertIn("收藏", fields["trigger"])

    def test_parse_scene(self):
        _, _, fields = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertNotEqual(fields["scene"], "待补充")

    def test_parse_tags(self):
        """V2 新增：笔记应包含标签"""
        _, _, fields = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertIn("知识管理", fields["tags"])
        self.assertIn("信息焦虑", fields["tags"])
        self.assertEqual(len(fields["tags"]), 3)

    def test_filename_format(self):
        filename, _, _ = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertTrue(filename.startswith("2026-04-10"))
        self.assertTrue(filename.endswith(".md"))

    def test_md_has_frontmatter(self):
        _, md, _ = sn.parse_note(self.note_text, "msg_001", "1712700000000")
        self.assertIn("---", md)
        self.assertIn("tags:", md)
        self.assertIn("crucible", md)

    def test_no_tags_defaults_empty(self):
        """没有标签字段时，tags 应为空列表"""
        text = "Crucible 笔记\n日期：2026-04-10\n洞察：测试"
        _, _, fields = sn.parse_note(text, "msg_002", "0")
        self.assertEqual(fields["tags"], [])

    def test_no_scene_defaults_pending(self):
        """没有应用场景时，应为待补充——对应测评用例 3"""
        text = "Crucible 笔记\n日期：2026-04-10\n洞察：测试"
        _, _, fields = sn.parse_note(text, "msg_003", "0")
        self.assertEqual(fields["scene"], "待补充")


class TestSegmentConversations(unittest.TestCase):
    """测试对话分段——间隔 30 分钟分段"""

    def _make_msg(self, create_time, text="hello", sender_type="user"):
        return {
            "create_time": str(create_time),
            "msg_type": "text",
            "body": {"content": json.dumps({"text": text})},
            "sender": {"sender_type": sender_type},
            "message_id": f"msg_{create_time}",
        }

    def test_single_conversation(self):
        """连续消息应归为同一对话"""
        msgs = [
            self._make_msg(1000000),
            self._make_msg(1000000 + 60000),  # +1 分钟
            self._make_msg(1000000 + 120000),  # +2 分钟
        ]
        convs = sn.segment_conversations(msgs)
        self.assertEqual(len(convs), 1)
        self.assertEqual(len(convs[0]), 3)

    def test_two_conversations(self):
        """间隔超过 30 分钟应分为两段对话"""
        msgs = [
            self._make_msg(1000000),
            self._make_msg(1000000 + 60000),
            self._make_msg(1000000 + 60000 + 1801000),  # +30 分钟以上
        ]
        convs = sn.segment_conversations(msgs)
        self.assertEqual(len(convs), 2)

    def test_skip_empty_messages(self):
        """空消息应被跳过"""
        msgs = [
            self._make_msg(1000000, text="hello"),
            self._make_msg(1000000 + 1000, text=""),
        ]
        convs = sn.segment_conversations(msgs)
        self.assertEqual(len(convs), 1)
        self.assertEqual(len(convs[0]), 1)

    def test_skip_error_messages(self):
        """HTTP 500 错误消息应被跳过"""
        msgs = [
            self._make_msg(1000000, text="hello"),
            self._make_msg(1000000 + 1000, text="HTTP 500 api_error"),
        ]
        convs = sn.segment_conversations(msgs)
        self.assertEqual(len(convs[0]), 1)


class TestAnalyzeConversation(unittest.TestCase):
    """测试对话分析——对应测评方案用例 5/13 的统计验证"""

    def _make_msg(self, create_time, text, sender_type, msg_id=None):
        return {
            "create_time": str(create_time),
            "msg_type": "text",
            "body": {"content": json.dumps({"text": text})},
            "sender": {"sender_type": sender_type},
            "message_id": msg_id or f"msg_{create_time}",
        }

    def test_completed_conversation(self):
        """有笔记的对话应标记为 completed"""
        msgs = [
            self._make_msg(1000, "我觉得AI很有趣", "user", "msg_1"),
            self._make_msg(2000, "为什么觉得有趣？", "app"),
            self._make_msg(3000, "因为能帮我思考", "user"),
            self._make_msg(4000, "Crucible 笔记\n个人应用场景\n用AI帮我理清思路", "app", "note_1"),
        ]
        metrics = sn.analyze_conversation(msgs)
        self.assertTrue(metrics["completed"])
        self.assertTrue(metrics["has_application"])
        self.assertEqual(metrics["turns"], 2)

    def test_incomplete_conversation(self):
        """没有笔记的对话应标记为未完成"""
        msgs = [
            self._make_msg(1000, "你好", "user", "msg_1"),
            self._make_msg(2000, "你好！", "app"),
        ]
        metrics = sn.analyze_conversation(msgs)
        self.assertFalse(metrics["completed"])

    def test_input_type_article(self):
        """含 URL 的输入应识别为 article"""
        msgs = [
            self._make_msg(1000, "https://example.com 这篇不错", "user", "msg_1"),
            self._make_msg(2000, "回复", "app"),
        ]
        metrics = sn.analyze_conversation(msgs)
        self.assertEqual(metrics["input_type"], "article")

    def test_input_type_thought(self):
        """纯文本输入应识别为 thought"""
        msgs = [
            self._make_msg(1000, "我觉得学习需要输出", "user", "msg_1"),
            self._make_msg(2000, "回复", "app"),
        ]
        metrics = sn.analyze_conversation(msgs)
        self.assertEqual(metrics["input_type"], "thought")


# ════════════════════════════════════════════════════════
# 集成测试（使用内存 SQLite）
# ════════════════════════════════════════════════════════


class TestDatabaseIntegration(unittest.TestCase):
    """测试数据库操作——对应测评方案用例 11-13"""

    def setUp(self):
        """用内存数据库，避免影响真实数据"""
        self.conn = sqlite3.connect(":memory:")
        # 复用 init_db 的建表逻辑
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_metrics (
                id TEXT PRIMARY KEY, created_at DATETIME, input_type TEXT,
                turns INTEGER, completed BOOLEAN, has_application BOOLEAN,
                user_msg_chars INTEGER, note_id TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS note_metrics (
                note_id TEXT PRIMARY KEY, created_at DATETIME,
                linked_count INTEGER DEFAULT 0, searched_hit INTEGER DEFAULT 0
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT,
                PRIMARY KEY (note_id, tag)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_digests (
                id TEXT PRIMARY KEY, created_at DATETIME,
                content_url TEXT, content_summary TEXT, tags TEXT,
                status TEXT DEFAULT 'pending', reminded_at DATETIME,
                digested_at DATETIME, note_id TEXT
            )
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_upsert_conversation(self):
        """测试对话指标写入"""
        metrics = {
            "id": "conv_1", "created_at": "2026-04-10T10:00:00",
            "input_type": "thought", "turns": 3, "completed": True,
            "has_application": True, "user_msg_chars": 150, "note_id": "note_1",
        }
        sn.upsert_conversation(self.conn, metrics)
        self.conn.commit()

        row = self.conn.execute(
            "SELECT * FROM conversation_metrics WHERE id = 'conv_1'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[3], 3)  # turns

    def test_upsert_conversation_update(self):
        """测试对话指标更新（重复写入应覆盖）"""
        metrics = {
            "id": "conv_1", "created_at": "2026-04-10T10:00:00",
            "input_type": "thought", "turns": 2, "completed": False,
            "has_application": False, "user_msg_chars": 50, "note_id": None,
        }
        sn.upsert_conversation(self.conn, metrics)
        metrics["turns"] = 4
        metrics["completed"] = True
        sn.upsert_conversation(self.conn, metrics)
        self.conn.commit()

        row = self.conn.execute(
            "SELECT turns, completed FROM conversation_metrics WHERE id = 'conv_1'"
        ).fetchone()
        self.assertEqual(row[0], 4)
        self.assertEqual(row[1], True)

    def test_upsert_tags(self):
        """测试标签写入——对应 V2 标签系统"""
        sn.upsert_note(self.conn, "note_1", "2026-04-10")
        sn.upsert_tags(self.conn, "note_1", ["知识管理", "AI", "学习方法"])
        self.conn.commit()

        tags = self.conn.execute(
            "SELECT tag FROM note_tags WHERE note_id = 'note_1' ORDER BY tag"
        ).fetchall()
        tag_names = [t[0] for t in tags]
        self.assertIn("知识管理", tag_names)
        self.assertIn("AI", tag_names)
        self.assertEqual(len(tag_names), 3)

    def test_find_related_notes(self):
        """测试标签关联——标签重合 ≥2 才算关联"""
        # 笔记 1：知识管理, AI, 学习
        sn.upsert_note(self.conn, "note_1", "2026-04-10")
        sn.upsert_tags(self.conn, "note_1", ["知识管理", "AI", "学习"])
        # 笔记 2：知识管理, AI（重合 2 个 → 应关联）
        sn.upsert_note(self.conn, "note_2", "2026-04-10")
        sn.upsert_tags(self.conn, "note_2", ["知识管理", "AI"])
        # 笔记 3：只有 AI（重合 1 个 → 不应关联）
        sn.upsert_note(self.conn, "note_3", "2026-04-10")
        sn.upsert_tags(self.conn, "note_3", ["AI"])
        self.conn.commit()

        related = sn.find_related_notes(self.conn, "note_1", min_overlap=2)
        related_ids = [r[0] for r in related]
        self.assertIn("note_2", related_ids)
        self.assertNotIn("note_3", related_ids)

    def test_find_related_notes_no_tags(self):
        """没有标签的笔记不应有关联"""
        sn.upsert_note(self.conn, "note_x", "2026-04-10")
        self.conn.commit()
        related = sn.find_related_notes(self.conn, "note_x")
        self.assertEqual(related, [])

    def test_build_tag_index(self):
        """测试 TAG_INDEX 构建"""
        sn.upsert_note(self.conn, "note_1", "2026-04-10")
        sn.upsert_tags(self.conn, "note_1", ["AI", "产品"])
        sn.upsert_note(self.conn, "note_2", "2026-04-11")
        sn.upsert_tags(self.conn, "note_2", ["AI"])
        self.conn.commit()

        index = sn.build_tag_index(self.conn)
        self.assertIn("AI", index["tags"])
        self.assertEqual(len(index["tags"]["AI"]), 2)
        self.assertIn("产品", index["tags"])

    def test_build_digest_report(self):
        """测试收藏消化报告——对应测评方案用例 9"""
        self.conn.execute("""
            INSERT INTO pending_digests (id, created_at, content_url, content_summary, tags, status)
            VALUES ('d1', '2026-04-05T10:00:00', 'https://example.com/a', 'AI产品设计', '["AI", "产品"]', 'pending')
        """)
        self.conn.execute("""
            INSERT INTO pending_digests (id, created_at, content_url, content_summary, tags, status)
            VALUES ('d2', '2026-04-06T10:00:00', 'https://example.com/b', 'AI面试准备', '["AI", "求职"]', 'pending')
        """)
        self.conn.commit()

        report = sn.build_digest_report(self.conn)
        self.assertIsNotNone(report)
        self.assertEqual(report["pending_count"], 2)
        self.assertGreater(len(report["clusters"]), 0)

    def test_build_digest_report_empty(self):
        """没有待消化收藏时应返回 None"""
        report = sn.build_digest_report(self.conn)
        self.assertIsNone(report)

    def test_pending_digest_status(self):
        """测试收藏状态为 pending 才会出现在报告中"""
        self.conn.execute("""
            INSERT INTO pending_digests (id, created_at, content_summary, tags, status)
            VALUES ('d1', '2026-04-05', '已消化', '[]', 'digested')
        """)
        self.conn.execute("""
            INSERT INTO pending_digests (id, created_at, content_summary, tags, status)
            VALUES ('d2', '2026-04-06', '待消化', '[]', 'pending')
        """)
        self.conn.commit()

        report = sn.build_digest_report(self.conn)
        self.assertEqual(report["pending_count"], 1)


class TestBookmarkUrlExtraction(unittest.TestCase):
    """测试收藏消息中的 URL 和摘要提取——对应 Plan 8 链接内容抓取"""

    def test_bookmark_with_url_and_summary(self):
        """正常 bookmark：Bot 抓到内容后回复摘要"""
        bot_text = "收到，已加入待消化。这篇讲的是如何用 AI 做知识管理"
        summary_match = re.search(r"已加入待消化[。.]\s*(.+?)$", bot_text)
        self.assertIsNotNone(summary_match)
        self.assertIn("知识管理", summary_match.group(1))

    def test_bookmark_fallback_unreachable_url(self):
        """fallback：链接打不开时仍应识别为 bookmark"""
        bot_text = "收到，已加入待消化。链接我暂时打不开，下次消化时你可以补充一下内容。"
        self.assertIn("已加入待消化", bot_text)
        summary_match = re.search(r"已加入待消化[。.]\s*(.+?)$", bot_text)
        self.assertIsNotNone(summary_match)

    def test_url_extraction_from_user_msg(self):
        """从用户消息中提取 URL"""
        user_text = "https://mp.weixin.qq.com/s/abc123 这篇不错"
        url_match = re.search(r"https?://\S+", user_text)
        self.assertIsNotNone(url_match)
        self.assertTrue(url_match.group(0).startswith("https://mp.weixin.qq.com"))

    def test_url_extraction_bare_url(self):
        """纯链接（无附加文字）"""
        user_text = "https://example.com/article/123"
        url_match = re.search(r"https?://\S+", user_text)
        self.assertIsNotNone(url_match)
        self.assertEqual(url_match.group(0), "https://example.com/article/123")

    def test_no_url_in_message(self):
        """纯文字消息不应匹配到 URL"""
        user_text = "我觉得知识管理很重要"
        url_match = re.search(r"https?://\S+", user_text)
        self.assertIsNone(url_match)

    def test_input_type_url_with_opinion(self):
        """URL + 想法应识别为 article（content_digest 场景）"""
        msgs = [
            {
                "create_time": "1000",
                "msg_type": "text",
                "body": {"content": json.dumps({"text": "https://example.com 我觉得这个观点很有意思"})},
                "sender": {"sender_type": "user"},
                "message_id": "msg_1",
            },
            {
                "create_time": "2000",
                "msg_type": "text",
                "body": {"content": json.dumps({"text": "这篇讲了XX，你最在意哪一点？"})},
                "sender": {"sender_type": "app"},
                "message_id": "msg_2",
            },
        ]
        metrics = sn.analyze_conversation(msgs)
        self.assertEqual(metrics["input_type"], "article")


class TestSyncState(unittest.TestCase):
    """测试同步状态管理"""

    def test_load_state_default(self):
        """没有状态文件时应返回空"""
        # 临时修改 STATE_FILE 指向不存在的路径
        original = sn.STATE_FILE
        sn.STATE_FILE = Path(tempfile.mktemp(suffix=".json"))
        try:
            state = sn.load_state()
            self.assertEqual(state, {"synced_msg_ids": []})
        finally:
            sn.STATE_FILE = original

    def test_save_and_load_state(self):
        """保存后应能读回"""
        original = sn.STATE_FILE
        tmp = Path(tempfile.mktemp(suffix=".json"))
        sn.STATE_FILE = tmp
        try:
            sn.save_state({"synced_msg_ids": ["msg_1", "msg_2"]})
            state = sn.load_state()
            self.assertEqual(len(state["synced_msg_ids"]), 2)
            self.assertIn("msg_1", state["synced_msg_ids"])
        finally:
            sn.STATE_FILE = original
            tmp.unlink(missing_ok=True)


class TestTagMaturity(unittest.TestCase):
    """测试知识成熟度"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
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
                tag TEXT PRIMARY KEY, maturity TEXT DEFAULT 'seed',
                conversation_count INTEGER DEFAULT 0, application_count INTEGER DEFAULT 0,
                linked_note_count INTEGER DEFAULT 0, score REAL DEFAULT 0.0,
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

    def test_maturity_seed(self):
        """只聊过 1 次、没有应用场景 → seed"""
        result = sn.compute_maturity_level(conv_count=1, app_count=0, linked_count=0)
        self.assertEqual(result["maturity"], "seed")
        self.assertLessEqual(result["score"], 0.3)

    def test_maturity_growing_by_conversations(self):
        """聊过 2-3 次 → growing"""
        result = sn.compute_maturity_level(conv_count=2, app_count=0, linked_count=0)
        self.assertEqual(result["maturity"], "growing")
        self.assertGreater(result["score"], 0.1)
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

    def test_update_tag_maturity_from_db(self):
        """从 DB 数据计算每个标签的成熟度"""
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
        self.assertEqual(row[0], "growing")
        self.assertEqual(row[1], 3)
        self.assertEqual(row[2], 2)

    def test_update_tag_maturity_seed(self):
        """只有 1 条笔记的标签 → seed"""
        self.conn.execute("INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')")
        self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '新话题', 'topic')")
        self.conn.execute(
            "INSERT INTO conversation_metrics (id, created_at, input_type, turns, completed, has_application, user_msg_chars, note_id) VALUES ('c1', '2026-04-01', 'thought', 2, 1, 0, 50, 'n1')"
        )
        self.conn.commit()

        sn.update_tag_maturity(self.conn)

        row = self.conn.execute("SELECT maturity FROM tag_maturity WHERE tag = ?", ("新话题",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "seed")

    def test_build_maturity_report(self):
        """生成成熟度报告 JSON"""
        now = "2026-04-11T12:00:00"
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
        self.assertEqual(report["tags"][0]["tag"], "远程办公")  # 按 score 降序

    def test_build_maturity_report_empty(self):
        """没有标签时返回 None"""
        report = sn.build_maturity_report(self.conn)
        self.assertIsNone(report)


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
                "INSERT INTO note_metrics (note_id, created_at) VALUES (?, '2026-04-01')", (f"n{i}",)
            )
            self.conn.execute(
                "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, '知行合一', 'topic')", (f"n{i}",)
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
                "INSERT INTO note_metrics (note_id, created_at) VALUES (?, '2026-04-01')", (f"n{i}",)
            )
            self.conn.execute(
                "INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, '新话题', 'topic')", (f"n{i}",)
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
                "INSERT INTO tag_maturity (tag, maturity, score) VALUES (?, ?, ?)", (tag, maturity, score)
            )
        self.conn.commit()

        themes = sn.detect_themes(self.conn)
        self.assertEqual(len(themes), 2)
        self.assertEqual(themes[0]["tag"], "远程办公")

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


class TestCognitiveChallenge(unittest.TestCase):
    """��试认知挑战（锻造）"""

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
                note_id TEXT PRIMARY KEY, challenge_type TEXT,
                challenged_at DATETIME, outcome TEXT
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
        self.conn.execute("INSERT INTO tag_maturity (tag, maturity, score) VALUES ('远程办公', 'growing', 0.5)")
        self.conn.execute("INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')")
        self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '远程办公', 'topic')")
        self.conn.commit()

        candidates = sn.find_challenge_candidates(self.conn)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["note_id"], "n1")

    def test_no_challenge_for_seed(self):
        """种子阶段的笔记 → 不挑战"""
        self.conn.execute("INSERT INTO tag_maturity (tag, maturity, score) VALUES ('新话题', 'seed', 0.1)")
        self.conn.execute("INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')")
        self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '新话题', 'topic')")
        self.conn.commit()

        candidates = sn.find_challenge_candidates(self.conn)
        self.assertEqual(len(candidates), 0)

    def test_no_challenge_for_already_challenged(self):
        """已经被挑战过的笔记 → 不重复挑战"""
        self.conn.execute("INSERT INTO tag_maturity (tag, maturity, score) VALUES ('远程办公', 'growing', 0.5)")
        self.conn.execute("INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')")
        self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '远程办公', 'topic')")
        self.conn.execute("INSERT INTO challenge_log (note_id, challenge_type, challenged_at, outcome) VALUES ('n1', 'counterexample', '2026-04-05', 'strengthened')")
        self.conn.commit()

        candidates = sn.find_challenge_candidates(self.conn)
        self.assertEqual(len(candidates), 0)

    def test_build_challenge_report(self):
        """生成挑战候选报告"""
        self.conn.execute("INSERT INTO tag_maturity (tag, maturity, score) VALUES ('远程办公', 'growing', 0.5)")
        self.conn.execute("INSERT INTO note_metrics (note_id, created_at) VALUES ('n1', '2026-04-01')")
        self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '远程办公', 'topic')")
        self.conn.commit()

        report = sn.build_challenge_report(self.conn)
        self.assertIsNotNone(report)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["note_id"], "n1")

    def test_build_challenge_report_empty(self):
        """没有候选时返回 None"""
        report = sn.build_challenge_report(self.conn)
        self.assertIsNone(report)


class TestExpressReady(unittest.TestCase):
    """测试 express_ready 标记和 Express 报告"""

    def test_express_ready_growing_high_score(self):
        """growing + score >= 0.35 → express_ready"""
        result = sn.compute_maturity_level(conv_count=3, app_count=1, linked_count=1)
        self.assertEqual(result["maturity"], "growing")
        self.assertTrue(result["express_ready"])

    def test_express_ready_mature(self):
        """mature → express_ready"""
        result = sn.compute_maturity_level(conv_count=5, app_count=3, linked_count=3)
        self.assertEqual(result["maturity"], "mature")
        self.assertTrue(result["express_ready"])

    def test_not_express_ready_seed(self):
        """seed → not express_ready"""
        result = sn.compute_maturity_level(conv_count=1, app_count=0, linked_count=0)
        self.assertEqual(result["maturity"], "seed")
        self.assertFalse(result["express_ready"])

    def test_not_express_ready_growing_low_score(self):
        """growing 但 score < 0.35 → not express_ready"""
        result = sn.compute_maturity_level(conv_count=2, app_count=0, linked_count=0)
        self.assertEqual(result["maturity"], "growing")
        # score = 2/5 * 0.4 = 0.16, < 0.35
        self.assertFalse(result["express_ready"])

    def test_express_ready_growing_with_application(self):
        """growing + 有应用场景 → score 够高 → express_ready"""
        result = sn.compute_maturity_level(conv_count=1, app_count=1, linked_count=1)
        self.assertEqual(result["maturity"], "growing")
        # score = 0.08 + 0.117 + 0.083 = ~0.28 -- hmm let me check
        # conv: 1/5 * 0.4 = 0.08, app: 1/3 * 0.35 = 0.117, link: 1/3 * 0.25 = 0.083
        # total = 0.28 < 0.35 → not express_ready
        self.assertFalse(result["express_ready"])

    def test_express_ready_boundary(self):
        """growing + score 刚好 >= 0.35 → express_ready"""
        # conv=3: 3/5*0.4=0.24, app=1: 1/3*0.35=0.117, link=0: 0
        # total = 0.357 → 0.36 >= 0.35 → express_ready
        result = sn.compute_maturity_level(conv_count=3, app_count=1, linked_count=0)
        self.assertEqual(result["maturity"], "growing")
        self.assertGreaterEqual(result["score"], 0.35)
        self.assertTrue(result["express_ready"])


class TestExpressReport(unittest.TestCase):
    """测试 Express 报告生成"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tag_maturity (
                tag TEXT PRIMARY KEY, maturity TEXT DEFAULT 'seed',
                conversation_count INTEGER DEFAULT 0, application_count INTEGER DEFAULT 0,
                linked_note_count INTEGER DEFAULT 0, score REAL DEFAULT 0.0, updated_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT, PRIMARY KEY (note_id, tag)
            );
        """)
        # 创建临时笔记目录
        self._tmp_dir = tempfile.mkdtemp()
        self._orig_note_dir = sn.NOTE_DIR
        sn.NOTE_DIR = Path(self._tmp_dir)

    def tearDown(self):
        sn.NOTE_DIR = self._orig_note_dir
        import shutil
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _write_note(self, note_id, insight, scenario="待补充", trigger="某个想法"):
        """写一个测试笔记文件"""
        content = f"""---
note_id: {note_id}
type: insight
tags: [crucible, 测试]
---
# {insight}

## 原始触发
{trigger}

## 追问摘要
经过追问深入了理解

> **洞察：** {insight}

## 个人应用场景
{scenario}
"""
        (Path(self._tmp_dir) / f"{note_id[:20]}.md").write_text(content, encoding="utf-8")

    def test_express_report_with_ready_topic(self):
        """有 express_ready 标签 → 生成报告"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, conversation_count, application_count, score) VALUES (?, ?, ?, ?, ?)",
            ("知识管理", "growing", 3, 1, 0.44)
        )
        for i in range(3):
            nid = f"note_{i}"
            self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES (?, '知识管理', 'topic')", (nid,))
            self._write_note(nid, f"洞察{i}", scenario="在工作中应用" if i == 0 else "待补充")
        self.conn.commit()

        report = sn.build_express_report(self.conn)
        self.assertIsNotNone(report)
        self.assertEqual(report["express_ready_count"], 1)
        topic = report["topics"][0]
        self.assertEqual(topic["tag"], "知识管理")
        self.assertEqual(len(topic["insights"]), 3)
        self.assertEqual(len(topic["scenarios"]), 1)

    def test_express_report_no_ready_topics(self):
        """没有 express_ready 标签 → 返回 None"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, conversation_count, application_count, score) VALUES (?, ?, ?, ?, ?)",
            ("新话题", "seed", 1, 0, 0.08)
        )
        self.conn.commit()

        report = sn.build_express_report(self.conn)
        self.assertIsNone(report)

    def test_express_report_empty_db(self):
        """空数据库 → 返回 None"""
        report = sn.build_express_report(self.conn)
        self.assertIsNone(report)

    def test_express_report_skips_low_score_growing(self):
        """growing 但 score 低 → 不进入报告"""
        self.conn.execute(
            "INSERT INTO tag_maturity (tag, maturity, conversation_count, application_count, score) VALUES (?, ?, ?, ?, ?)",
            ("新方向", "growing", 2, 0, 0.16)
        )
        self.conn.execute("INSERT INTO note_tags (note_id, tag, tag_type) VALUES ('n1', '新方向', 'topic')")
        self._write_note("n1", "一个洞察")
        self.conn.commit()

        report = sn.build_express_report(self.conn)
        self.assertIsNone(report)


class TestExpressLog(unittest.TestCase):
    """测试 Express 输出记录"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tag_maturity (
                tag TEXT PRIMARY KEY, maturity TEXT DEFAULT 'seed',
                conversation_count INTEGER DEFAULT 0, application_count INTEGER DEFAULT 0,
                linked_note_count INTEGER DEFAULT 0, score REAL DEFAULT 0.0, updated_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT, PRIMARY KEY (note_id, tag)
            );
            CREATE TABLE IF NOT EXISTS express_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL,
                title TEXT, content TEXT, platform TEXT DEFAULT 'xiaohongshu',
                published_at TEXT, source_note_ids TEXT,
                archived BOOLEAN DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
            );
        """)

    def test_log_express_basic(self):
        """记录一次输出"""
        self.conn.execute("INSERT INTO note_tags VALUES ('n1', '知识管理', 'topic')")
        self.conn.execute("INSERT INTO note_tags VALUES ('n2', '知识管理', 'topic')")
        self.conn.commit()

        row_id = sn.log_express(self.conn, "知识管理", "我的知识管理心得", "正文内容")
        self.assertIsNotNone(row_id)

        row = self.conn.execute("SELECT * FROM express_log WHERE id = ?", (row_id,)).fetchone()
        self.assertEqual(row[1], "知识管理")  # tag
        self.assertEqual(row[2], "我的知识管理心得")  # title
        self.assertEqual(row[4], "xiaohongshu")  # platform
        source_ids = json.loads(row[6])
        self.assertEqual(set(source_ids), {"n1", "n2"})

    def test_log_express_no_notes(self):
        """标签下无笔记也能记录"""
        row_id = sn.log_express(self.conn, "新标签", "标题", "内容")
        self.assertIsNotNone(row_id)
        row = self.conn.execute("SELECT source_note_ids FROM express_log WHERE id = ?", (row_id,)).fetchone()
        self.assertEqual(json.loads(row[0]), [])

    def test_multiple_express_logs(self):
        """同一标签可以多次输出"""
        sn.log_express(self.conn, "知识管理", "第一篇", "内容1")
        sn.log_express(self.conn, "知识管理", "第二篇", "内容2")
        count = self.conn.execute("SELECT COUNT(*) FROM express_log").fetchone()[0]
        self.assertEqual(count, 2)


class TestExpressArchive(unittest.TestCase):
    """测试 Express 输出归档为 Obsidian 笔记"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id TEXT, tag TEXT, tag_type TEXT, PRIMARY KEY (note_id, tag)
            );
            CREATE TABLE IF NOT EXISTS express_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL,
                title TEXT, content TEXT, platform TEXT DEFAULT 'xiaohongshu',
                published_at TEXT, source_note_ids TEXT,
                archived BOOLEAN DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._tmp_dir = tempfile.mkdtemp()
        self._orig_note_dir = sn.NOTE_DIR
        sn.NOTE_DIR = Path(self._tmp_dir)

    def tearDown(self):
        sn.NOTE_DIR = self._orig_note_dir
        import shutil
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_archive_creates_note(self):
        """归档生成 Obsidian 笔记文件"""
        self.conn.execute("""
            INSERT INTO express_log (tag, title, content, platform, published_at, source_note_ids, archived)
            VALUES ('知识管理', '我的心得', '正文内容blah', 'xiaohongshu', '2026-04-12T10:00:00', '["n1","n2"]', 0)
        """)
        self.conn.commit()

        archived = sn.archive_express_notes(self.conn)
        self.assertEqual(archived, 1)

        # 检查文件
        files = list(Path(self._tmp_dir).glob("*express*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("type: express", content)
        self.assertIn("我的心得", content)
        self.assertIn("正文内容blah", content)
        self.assertIn("platform: xiaohongshu", content)

    def test_archive_marks_as_archived(self):
        """归档后 archived 标记为 1"""
        self.conn.execute("""
            INSERT INTO express_log (tag, title, content, published_at, source_note_ids, archived)
            VALUES ('测试', '标题', '内容', '2026-04-12T10:00:00', '[]', 0)
        """)
        self.conn.commit()

        sn.archive_express_notes(self.conn)
        row = self.conn.execute("SELECT archived FROM express_log").fetchone()
        self.assertEqual(row[0], 1)

    def test_archive_skips_already_archived(self):
        """已归档的不重复归档"""
        self.conn.execute("""
            INSERT INTO express_log (tag, title, content, published_at, source_note_ids, archived)
            VALUES ('测试', '标题', '内容', '2026-04-12T10:00:00', '[]', 1)
        """)
        self.conn.commit()

        archived = sn.archive_express_notes(self.conn)
        self.assertEqual(archived, 0)

    def test_archive_no_records(self):
        """无记录时返回 0"""
        archived = sn.archive_express_notes(self.conn)
        self.assertEqual(archived, 0)

    def test_archive_with_source_links(self):
        """归档笔记包含来源笔记链接"""
        # 创建一个源笔记
        source_content = """---
note_id: src_note_1
type: insight
---
# 测试洞察
"""
        (Path(self._tmp_dir) / "src_note_1.md").write_text(source_content, encoding="utf-8")

        self.conn.execute("""
            INSERT INTO express_log (tag, title, content, published_at, source_note_ids, archived)
            VALUES ('测试', '标题', '内容', '2026-04-12T10:00:00', '["src_note_1"]', 0)
        """)
        self.conn.commit()

        sn.archive_express_notes(self.conn)

        files = list(Path(self._tmp_dir).glob("*express*.md"))
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("[[src_note_1]]", content)


if __name__ == "__main__":
    unittest.main()
