#!/usr/bin/env python3
"""
Crucible sync-notes.py 测试

单元测试：测试核心解析、分段、分析函数（不依赖网络和数据库）
集成测试：测试数据库读写 + 标签关联 + 收藏消化报告（用内存 SQLite）
"""

import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main()
