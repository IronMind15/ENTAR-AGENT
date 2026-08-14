"""看板模板系统测试（v1.12.0）

覆盖：系统种子（daily 逐字回归锚点）、用户私有隔离、订阅 template_id 迁移、
section_spec 渲染（多结论区分流）、模板指令透传、描述成模板（LLM mock）、
提交模板解析（Excel/Markdown 表头）、订阅命令意图、技能切换/保存流程。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard.template_store import (  # noqa: E402
    DashboardTemplate,
    TemplateStore,
    _DEFAULT_SECTION_SPEC,
    _SYSTEM_TEMPLATES,
)
from dashboard.subscription_store import (  # noqa: E402
    Subscription,
    SubscriptionStore,
)


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _cleanup_db(path, store):
    try:
        store.close()
    except Exception:
        pass
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


class TemplateStoreTests(unittest.TestCase):
    def setUp(self):
        self._path = _tmp_db()
        self.store = TemplateStore(db_path=self._path)
        self.addCleanup(lambda: _cleanup_db(self._path, self.store))

    def test_sync_user_template_file_writes_json(self):
        """v1.12.x：用户模板同步为本地用户文件夹下的 JSON 文件"""
        import json
        import dashboard.template_store as ts
        res = self.store.create_user_template(
            key="my_tpl", name="我的模板", user_id="union001",
            description="先结论后分块", map_instructions="只看变化",
            reduce_instructions="先总体后各表",
            section_spec=_DEFAULT_SECTION_SPEC)
        self.assertTrue(res["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ts, "_TEMPLATE_DIR", tmp):
                path = TemplateStore.sync_user_template_file(
                    res["template"], staff_id="staff001")
            self.assertTrue(path.startswith(tmp))
            self.assertTrue(os.path.exists(path))
            payload = json.load(open(path, encoding="utf-8"))
            self.assertEqual(payload["key"], "my_tpl")
            self.assertEqual(payload["name"], "我的模板")
            self.assertEqual(payload["reduce_instructions"], "先总体后各表")
            self.assertEqual(payload["section_spec"], _DEFAULT_SECTION_SPEC)
            # 目录按 staff_id 隔离
            self.assertIn("staff001", path)

    def test_sync_skips_system_template(self):
        """系统模板不落文件（只记录用户自定义要求）"""
        import dashboard.template_store as ts
        daily = self.store.get("daily")
        with mock.patch.object(ts, "_TEMPLATE_DIR", tempfile.mkdtemp()):
            path = TemplateStore.sync_user_template_file(daily, staff_id="staff001")
        self.assertEqual(path, "")

    def test_remove_user_template_file(self):
        """删除模板同步文件（尽力而为）"""
        import dashboard.template_store as ts
        res = self.store.create_user_template(
            key="gone", name="要删的", user_id="union001")
        self.assertTrue(res["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ts, "_TEMPLATE_DIR", tmp):
                path = TemplateStore.sync_user_template_file(
                    res["template"], staff_id="staff001")
                self.assertTrue(os.path.exists(path))
                TemplateStore.remove_user_template_file("gone", "staff001")
                self.assertFalse(os.path.exists(path))

    def test_system_seeds_exist_and_daily_is_regression_anchor(self):
        keys = [t.key for t in self.store.list_visible()]
        self.assertEqual(sorted(keys), ["daily", "project", "weekly"])
        daily = self.store.get("daily")
        self.assertEqual(daily.scope, "system")
        # daily section_spec 必须与硬编码默认一致（_render spec=None 用它做逐字锚点）
        self.assertEqual(daily.section_spec, _DEFAULT_SECTION_SPEC)
        # 3 个种子的 key/name/section_spec 与代码定义一致
        for tpl in _SYSTEM_TEMPLATES:
            saved = self.store.get(tpl["key"])
            self.assertEqual(saved.name, tpl["name"])
            self.assertEqual(saved.section_spec, tpl["section_spec"])

    def test_user_template_isolated_per_owner(self):
        res = self.store.create_user_template(
            key="mytpl", name="我的模板", user_id="user001",
            map_instructions="只看变化", section_spec=_DEFAULT_SECTION_SPEC)
        self.assertTrue(res["ok"])
        # 本人可见
        self.assertIsNotNone(self.store.get("mytpl", "user001"))
        # 他人不可见、不可读
        keys = [t.key for t in self.store.list_visible("user002")]
        self.assertNotIn("mytpl", keys)
        self.assertIsNone(self.store.get("mytpl", "user002"))

    def test_create_user_template_rejects_system_key_and_duplicate(self):
        res = self.store.create_user_template(
            key="daily", name="复制", user_id="u1")
        self.assertFalse(res["ok"])
        self.assertIn("系统", res["message"])
        self.assertTrue(self.store.create_user_template(
            key="ok1", name="同名", user_id="u1")["ok"])
        dup = self.store.create_user_template(
            key="ok2", name="同名", user_id="u1")
        self.assertFalse(dup["ok"])

    def test_delete_only_own_user_template(self):
        self.assertTrue(self.store.create_user_template(
            key="t1", name="我的", user_id="u1")["ok"])
        self.assertFalse(self.store.delete_user_template("daily", "u1"))  # 系统不可删
        self.assertFalse(self.store.delete_user_template("t1", "u2"))    # 他人不可删
        self.assertTrue(self.store.delete_user_template("t1", "u1"))
        self.assertIsNone(self.store.get("t1", "u1"))

    def test_resolve_by_name_alias_and_key(self):
        self.assertEqual(self.store.resolve("看板用周报模板", "u1").key, "weekly")
        self.assertEqual(self.store.resolve("用项目看板", "u1").key, "project")
        self.assertEqual(self.store.resolve("daily", "u1").key, "daily")
        self.assertIsNone(self.store.resolve("没有这个模板", "u1"))


class SubscriptionTemplateMigrationTests(unittest.TestCase):
    def setUp(self):
        self._path = _tmp_db()
        self.store = SubscriptionStore(db_path=self._path)
        self.addCleanup(lambda: _cleanup_db(self._path, self.store))

    def _sub(self, **kw):
        base = Subscription(
            owner_user_id="user001", owner_staff_id="staff001",
            owner_union_id="union001", data_sources=["project_status"],
            push_hour=9, push_minute=0, weekdays="", alert_mode="always",
            recipients=["staff001"], title="恩特能源每日项目看板",
        )
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def test_template_id_defaults_to_daily(self):
        sub_id = self.store.create(self._sub())
        got = self.store.get(sub_id)
        self.assertEqual(got.template_id, "daily")

    def test_create_and_read_custom_template_id(self):
        sub_id = self.store.create(self._sub(template_id="weekly"))
        self.assertEqual(self.store.get(sub_id).template_id, "weekly")
        self.store.update(self.store.get(sub_id))
        self.assertEqual(self.store.get(sub_id).template_id, "weekly")

    def test_fingerprint_includes_template_id(self):
        a = self._sub(template_id="daily").fingerprint()
        b = self._sub(template_id="weekly").fingerprint()
        self.assertNotEqual(a, b)

    def test_legacy_table_gets_template_id_via_alter(self):
        """老库无 template_id 列：SubscriptionStore 初始化应 ALTER 补列"""
        import sqlite3
        # setUp 已用 SubscriptionStore 建过带 template_id 的表，这里用全新库模拟老库
        self._path = _tmp_db()
        conn = sqlite3.connect(self._path)
        conn.execute("""
            CREATE TABLE dashboard_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id TEXT NOT NULL,
                owner_staff_id TEXT DEFAULT '',
                owner_union_id TEXT DEFAULT '',
                data_sources TEXT DEFAULT '[]',
                push_hour INTEGER DEFAULT 9,
                push_minute INTEGER DEFAULT 0,
                weekdays TEXT DEFAULT '',
                alert_mode TEXT DEFAULT 'always',
                recipients TEXT DEFAULT '[]',
                title TEXT DEFAULT '恩特能源每日项目看板',
                last_snapshot TEXT DEFAULT 'null',
                last_pushed_at TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )""")
        conn.commit()
        conn.close()
        self.store.close()  # 释放 setUp 建的旧连接，避免 WAL 文件锁
        store = SubscriptionStore(db_path=self._path)  # 触发 _ensure_column
        self.addCleanup(store.close)
        cols = [r[1] for r in store._get_conn().execute(
            "PRAGMA table_info(dashboard_subscriptions)").fetchall()]
        self.assertIn("template_id", cols)
        sub_id = store.create(self._sub())
        self.assertEqual(store.get(sub_id).template_id, "daily")


class RenderTemplateTests(unittest.TestCase):
    """_render 的 section_spec 驱动输出"""

    @classmethod
    def setUpClass(cls):
        from dashboard.llm_pipeline import _render
        cls._render = _render
        cls.results = [{
            "source_key": "s1", "name": "项目A", "total": 5,
            "table_name": "", "status_counts": {},
            "source_meta": {"captured_at": "2026-08-12 09:00"},
            "detailed_items": [],
        }]
        cls.labels = {"s1": "S1", "s1:r1": "S1-R1"}
        cls.claims = [
            {"text": "本周上线了版本V2", "level": "update", "refs": ["s1:r1"],
             "evidence": [{"ref": "s1:r1", "field": "进展", "value": "已上线"}]},
            {"text": "供应商延期存在阻塞", "level": "risk", "refs": ["s1:r1"],
             "evidence": [{"ref": "s1:r1", "field": "风险", "value": "延期2周"}]},
        ]

    def _r(self, **kw):
        defaults = dict(results=self.results, claims=self.claims, labels=self.labels,
                        title="恩特能源每日项目看板", date_str="2026-08-12",
                        headline="今日变化 2 条",
                        valid_refs={"s1:r1": {
                            "evidence": {"source_name": "项目A"},
                            "fields": {"进展": "已上线", "风险": "延期2周"},
                        }})
        defaults.update(kw)
        # __func__：类属性函数经实例访问会被绑定成方法（实例变 results 位置参数），
        # 导致 TypeError: got multiple values for 'results'——须取原始函数再按关键字调用
        return self._render.__func__(**defaults)

    def test_daily_spec_is_verbatim_regression_anchor(self):
        """daily 模板输出 == 不传 spec 的输出（新格式逐字回归锚点）

        v1.12.x 改版：总体结论（跨来源）先行 → 各表最新总结（按来源分块）
        → 数据来源（原文链接）。本用例无跨来源结论，「总体结论」区不渲染。
        """
        expected = (
            "# 恩特能源每日项目看板\n\n"
            "> 数据日期：2026-08-12\n\n"
            "📌 今日要点：今日变化 2 条\n\n"
            "## 各表最新总结\n"
            "### 项目A\n"
            "- 🔵 本周上线了版本V2 [S1-R1]\n"
            "  - 依据原值：进展：已上线\n"
            "- 🔴 供应商延期存在阻塞 [S1-R1]\n"
            "  - 依据原值：风险：延期2周\n\n"
            "## 数据来源\n"
            "- [S1] 项目A：5 条，采集：2026-08-12 09:00\n"
        )
        self.assertEqual(self._r(spec=None), expected)
        self.assertEqual(self._r(spec=_DEFAULT_SECTION_SPEC), expected)
        self.assertEqual(self._r(spec=_DEFAULT_SECTION_SPEC),
                         self._r(spec=None))

    def test_daily_spec_groups_claims_by_source(self):
        """v1.12.x：各表最新总结按来源分块；跨来源结论先行归「总体」"""
        results = [
            dict(self.results[0]),
            {"source_key": "s2", "name": "项目B", "total": 3,
             "table_name": "", "status_counts": {},
             "source_meta": {"captured_at": "2026-08-12 09:00"},
             "detailed_items": []},
        ]
        claims = self.claims + [
            {"text": "两台整机均完成出厂测试", "level": "update",
             "refs": ["s1:r1", "s2:r9"],
             "evidence": [{"ref": "s1:r1", "field": "进展", "value": "已上线"}]},
            {"text": "B 项目物料到齐", "level": "info", "refs": ["s2:r9"],
             "evidence": [{"ref": "s2:r9", "field": "物料", "value": "到齐"}]},
        ]
        valid_refs = {
            "s1:r1": {"evidence": {"source_name": "项目A"},
                      "fields": {"进展": "已上线"}},
            "s2:r9": {"evidence": {"source_name": "项目B"},
                      "fields": {"物料": "到齐"}},
        }
        text = self._r(results=results, claims=claims, valid_refs=valid_refs,
                       spec=_DEFAULT_SECTION_SPEC)
        # 总体结论先行，然后按数据源顺序分表
        self.assertLess(text.index("## 总体结论"), text.index("## 各表最新总结"))
        self.assertLess(text.index("### 项目A"), text.index("### 项目B"))
        # 跨来源结论只在「总体结论」区出现一次，各表区不重复
        self.assertEqual(text.count("两台整机均完成出厂测试"), 1)
        self.assertIn("B 项目物料到齐", text)
        self.assertNotIn("### 📊 总体", text)  # 各表区不再收跨来源
        # 无跨来源结论时总体区整体不渲染
        text_no_cross = self._r(results=results, claims=self.claims,
                                valid_refs=valid_refs,
                                spec=_DEFAULT_SECTION_SPEC)
        self.assertNotIn("## 总体结论", text_no_cross)

    def test_truncate_sentence_on_boundary(self):
        """v1.12.x：证据原值超长时按句边界截断，不砍半句话"""
        from dashboard.llm_pipeline import _truncate_sentence
        long = ("7.5：1000V125KW运行20min无异常，通讯风扇正常，上位机参数已更改。"
                "7.4：与32号对拖老化完成，过程中风扇未异响。"
                "7.3：1000V 125KW运行15min风扇异响，待维修复测，通讯正常。"
                "7.1：接货后更换C相半桥板并调整风扇防护罩。")
        self.assertGreater(len(long), 60)  # 前置：数据确实超限
        out = _truncate_sentence(long, 60)
        self.assertTrue(out.endswith("…"))
        self.assertLess(len(out), len(long))
        # 结尾一定是完整句（句号/分号等之后截），不是砍半句
        body = out[:-1]
        self.assertTrue(body.rstrip()[-1:] in ("。", "；", "！", "？"))
        # 不超限则原样（无省略号）
        short = "已上线"
        self.assertEqual(_truncate_sentence(short, 60), short)
        # 句号稀疏（无边界）时退化硬截但仍有省略号标记
        dense = "A" * 100
        self.assertEqual(_truncate_sentence(dense, 60), "A" * 60 + "…")

    def test_weekly_spec_splits_claims_into_two_sections(self):
        spec = [
            {"kind": "headline", "title": "本周要点"},
            {"kind": "claims", "title": "本周进展", "levels": ["update", "info"]},
            {"kind": "claims", "title": "风险与待决策", "levels": ["risk", "decision"]},
            {"kind": "errors", "title": "数据完整性提醒"},
            {"kind": "sources", "title": "数据来源"},
        ]
        text = self._r(spec=spec)
        self.assertIn("📌 本周要点：今日变化 2 条", text)
        self.assertIn("## 本周进展", text)
        self.assertIn("本周上线了版本V2", text)
        self.assertIn("## 风险与待决策", text)
        self.assertIn("供应商延期存在阻塞", text)
        self.assertIn("## 数据来源", text)
        # 无错误时完整性提醒不渲染
        self.assertNotIn("数据完整性提醒", text)

    def test_empty_claims_fallback_only_first_claims_section(self):
        spec = [
            {"kind": "headline", "title": "今日要点"},
            {"kind": "claims", "title": "进展", "levels": ["update", "info"]},
            {"kind": "claims", "title": "风险", "levels": ["risk", "decision"]},
            {"kind": "sources", "title": "数据来源"},
        ]
        text = self._r(claims=[], spec=spec)
        # 兜底文案只出现一次（第一个结论区）
        self.assertEqual(text.count("当前没有可由来源记录支持的结论"),
                         1)
        self.assertIn("## 进展", text)

    def test_collection_errors_render_section(self):
        spec = _DEFAULT_SECTION_SPEC
        text = self._r(collection_errors=["来源A 读取失败", "来源B 超时"])
        self.assertIn("## 数据完整性提醒", text)
        self.assertIn("- ⚠️ 来源A 读取失败", text)


class BuildDashboardTemplateTests(unittest.TestCase):
    """build_dashboard_report 的模板指令/结构透传"""

    def _parsed(self):
        from dashboard.config_model import FieldSpec, SourceConfig
        from dashboard.parser import parse_source_records
        source = SourceConfig(
            key="future_board", name="未来新增看板", kind="notable",
            base_id="node-1",
            source_url="https://alidocs.dingtalk.com/i/nodes/node-1",
            field_map={
                "name": FieldSpec(label="事项名称", type="string"),
                "progress": FieldSpec(label="详细进展", type="string"),
            })
        return parse_source_records(source, [
            {"recordId": "r2", "fields": {"name": "重点", "progress": "今天完成"}},
        ])

    def _daily_template(self):
        from dashboard.template_store import _DEFAULT_MAP_INSTRUCTION
        from dashboard.template_store import _DEFAULT_REDUCE_INSTRUCTION
        return DashboardTemplate(
            key="daily", name="每日简报", scope="system",
            map_instructions=_DEFAULT_MAP_INSTRUCTION,
            reduce_instructions=_DEFAULT_REDUCE_INSTRUCTION,
            section_spec=list(_DEFAULT_SECTION_SPEC))

    def _make_fake_llm(self, prompts):
        def fake_llm(prompt, max_tokens=4000):
            prompts.append(prompt)
            if '"stage": "reduce"' in prompt:
                return ('{"claims":[{"text":"重点今天完成","level":"update",'
                        '"refs":["future_board:r2"],"evidence":['
                        '{"ref":"future_board:r2","field":"详细进展"}]}]}')
            return '{"selected_refs":["future_board:r2"]}'
        return fake_llm

    def test_custom_instructions_are_passed_to_llm(self):
        from dashboard.llm_pipeline import build_dashboard_report
        prompts = []
        tpl = DashboardTemplate(
            key="weekly", name="周报", scope="system",
            map_instructions="优先本周变化，只留风险",
            reduce_instructions="按进展与风险分列",
            section_spec=[{"kind": "claims", "title": "重点更新",
                           "levels": ["update", "info"]}],
        )
        build_dashboard_report([self._parsed()], None, self._make_fake_llm(prompts),
                               batch_size=1, max_batch_chars=100000, template=tpl)
        map_prompt = next(p for p in prompts if '"stage": "map"' in p)
        reduce_prompt = next(p for p in prompts if '"stage": "reduce"' in p)
        self.assertIn("优先本周变化，只留风险", map_prompt)
        self.assertIn("按进展与风险分列", reduce_prompt)

    def test_daily_template_equals_default_output(self):
        from dashboard.llm_pipeline import build_dashboard_report
        prompts = []
        fake = self._make_fake_llm(prompts)
        base = build_dashboard_report([self._parsed()], None, fake,
                                      batch_size=1, max_batch_chars=100000)
        prompts2 = []
        with_tpl = build_dashboard_report(
            [self._parsed()], None, self._make_fake_llm(prompts2),
            batch_size=1, max_batch_chars=100000, template=self._daily_template())
        self.assertEqual(base.text, with_tpl.text)

    def test_template_title_override(self):
        from dashboard.llm_pipeline import build_dashboard_report
        tpl = self._daily_template()
        tpl.title = "老板看板"
        report = build_dashboard_report([self._parsed()], None, None,
                                        template=tpl)
        self.assertIn("# 老板看板", report.text)

    def test_weekly_template_renders_two_sections(self):
        from dashboard.llm_pipeline import build_dashboard_report
        spec = [
            {"kind": "headline", "title": "本周要点"},
            {"kind": "claims", "title": "本周进展", "levels": ["update", "info"]},
            {"kind": "claims", "title": "风险与待决策", "levels": ["risk", "decision"]},
            {"kind": "sources", "title": "数据来源"},
        ]
        tpl = DashboardTemplate(key="weekly", name="周报", scope="system",
                                section_spec=spec)
        report = build_dashboard_report([self._parsed()], None, None, template=tpl)
        self.assertIn("## 本周进展", report.text)
        self.assertIn("## 数据来源", report.text)


class TemplateBuilderTests(unittest.TestCase):
    def test_parse_markdown_headings_to_spec(self):
        from dashboard.template_builder import parse_template_file
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "周报模板.md"
            path.write_text(
                "# 本周要点\n"
                "一些说明\n"
                "## 本周进展\n"
                "细节\n"
                "## 风险与待决策\n"
                "细节\n"
                "## 数据来源\n",
                encoding="utf-8")
            res = parse_template_file(str(path), "周报模板.md")
        self.assertTrue(res["ok"])
        kinds = [s["kind"] for s in res["section_spec"]]
        self.assertIn("headline", kinds)
        self.assertEqual(kinds.count("claims"), 2)
        self.assertIn("sources", kinds)

    def test_parse_excel_header_row(self):
        import openpyxl
        from dashboard.template_builder import parse_template_file
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "表模板.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["今日要点", "本周进展", "风险与阻塞", "数据完整性提醒", "数据来源"])
            ws.append(["内容", "内容", "内容", "内容", "内容"])
            wb.save(path)
            res = parse_template_file(str(path), "表模板.xlsx")
        self.assertTrue(res["ok"])
        kinds = [s["kind"] for s in res["section_spec"]]
        self.assertIn("headline", kinds)
        self.assertIn("errors", kinds)
        self.assertIn("sources", kinds)
        titles = [s["title"] for s in res["section_spec"]]
        self.assertIn("风险与阻塞", titles)

    def test_parse_missing_file_and_unsupported_ext(self):
        from dashboard.template_builder import parse_template_file
        res = parse_template_file("/不存在/文件.md", "文件.md")
        self.assertFalse(res["ok"])
        res = parse_template_file(__file__, "测试.py")
        self.assertFalse(res["ok"])
        self.assertIn("只支持", res["message"])

    def test_describe_to_spec_mock_llm(self):
        from dashboard.template_builder import describe_to_spec

        def fake_llm(prompt, max_tokens=4000):
            return ('{"name": "晨会看板", '
                    '"map_instructions": "优先今日变化", '
                    '"reduce_instructions": "按板块组织", '
                    '"section_spec": ['
                    '{"kind": "headline", "title": "今日要点"},'
                    '{"kind": "claims", "title": "进展", "levels": ["update", "info"]},'
                    '{"kind": "claims", "title": "风险", "levels": ["risk", "decision"]}'
                    "]}")

        res = describe_to_spec("晨会用，负责人/今日进展/明日计划", fake_llm)
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "晨会看板")
        self.assertEqual(res["map_instructions"], "优先今日变化")
        # 核心章节兜底：缺 errors/sources 自动补齐
        kinds = [s["kind"] for s in res["section_spec"]]
        self.assertIn("headline", kinds)
        self.assertEqual(kinds.count("claims"), 2)
        self.assertIn("errors", kinds)
        self.assertIn("sources", kinds)

    def test_describe_to_spec_llm_error_fallback(self):
        from dashboard.template_builder import describe_to_spec

        def broken_llm(prompt, max_tokens=4000):
            raise RuntimeError("API 挂了")

        res = describe_to_spec("测试描述", broken_llm)
        self.assertFalse(res["ok"])

    def test_describe_to_spec_empty_description(self):
        from dashboard.template_builder import describe_to_spec
        res = describe_to_spec("   ")
        self.assertFalse(res["ok"])


class SubscriptionCommandTemplateTests(unittest.TestCase):
    def test_template_intents(self):
        from dashboard import subscription_commands as sc
        cases = [
            ("看板模板", "template"),
            ("有什么看板模板", "template"),
            ("看板用周报模板", "set_template"),
            ("用周报模板做看板", "set_template"),
            ("按周报模板做看板", "set_template"),
            ("按这个格式做看板：负责人/今日进展/明日计划", "describe_template"),
            ("把这个当看板模板", "submit_template"),
        ]
        for text, expected in cases:
            parsed = sc.parse_subscription_command(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(parsed["intent"], expected, text)

    def test_describe_extracts_description(self):
        from dashboard import subscription_commands as sc
        parsed = sc.parse_subscription_command(
            "按这个格式做看板：负责人/今日进展/明日计划")
        self.assertEqual(parsed["description"], "负责人/今日进展/明日计划")

    def test_edit_template_intents(self):
        """v1.12.1 冲突矩阵：编辑 vs 切换 vs 描述/提交/列表"""
        from dashboard import subscription_commands as sc
        cases = [
            # (文本, 期望意图, 期望 description)
            ("编辑看板模板周报", "edit_template", ""),
            ("编辑周报看板模板", "edit_template", ""),
            ("编辑看板模板", "edit_template", ""),
            ("编辑看板模板周报改成：先写总体结论", "edit_template", "先写总体结论"),
            ("把周报模板改成：先写结论", "edit_template", "先写结论"),
            ("编辑周报模板，改成：先写结论", "edit_template", "先写结论"),
            # 无冒号格式 = 切换（set 不被编辑误抢）
            ("把看板模板改成周报", "set_template", None),
            ("用周报模板做看板", "set_template", None),
            # describe/submit/list 不受影响
            ("按这个格式做看板：负责人/进展", "describe_template", "负责人/进展"),
            ("把这个当看板模板", "submit_template", None),
            ("有哪些看板模板", "template", None),
        ]
        for text, expected, desc in cases:
            parsed = sc.parse_subscription_command(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(parsed["intent"], expected, text)
            if desc is None:
                self.assertNotIn("description", parsed, text)
            else:
                self.assertEqual(parsed.get("description", ""), desc, text)

    def test_extract_edit_description_only_with_colon(self):
        """desc 只认带冒号的「改成/改为/换成：」，防无冒号切换被当格式"""
        from dashboard import subscription_commands as sc
        # 有冒号 → 提取格式描述
        self.assertEqual(sc._extract_edit_template("编辑周报模板改成：先写总体结论"),
                         "先写总体结论")
        self.assertEqual(sc._extract_edit_template("把看板模板改成：每日先列风险"),
                         "每日先列风险")
        # 无冒号：编辑意图但 desc 空 / 无编辑动词则非编辑意图
        self.assertEqual(sc._extract_edit_template("编辑看板模板"), "")
        self.assertIsNone(sc._extract_edit_template("把看板模板改成周报"))
        # 非编辑意图 → None
        self.assertIsNone(sc._extract_edit_template("看板用周报模板"))
        self.assertIsNone(sc._extract_edit_template("有哪些看板模板"))

    def test_template_query_is_direct_not_write(self):
        from dashboard import subscription_commands as sc
        parsed = sc.parse_subscription_command("看板模板")
        self.assertNotIn("template_key", parsed)

    def test_render_confirmation_for_set_and_describe(self):
        from dashboard import subscription_commands as sc
        set_pending = {"intent": "set_template", "template_key": "weekly",
                       "template_name": "周报总结"}
        text = sc.render_confirmation(set_pending)
        self.assertIn("周报总结", text)
        self.assertIn("确认", text)
        describe_pending = {
            "intent": "describe_template", "template_name": "晨会看板",
            "section_spec": [{"kind": "headline", "title": "今日要点"},
                             {"kind": "claims", "title": "进展",
                              "levels": ["update", "info"]}],
        }
        text = sc.render_confirmation(describe_pending)
        self.assertIn("晨会看板", text)
        self.assertIn("今日要点 / 进展", text)

    def test_render_confirmation_edit_template(self):
        """v1.12.1：编辑模板确认文案（覆盖保存，非新建）"""
        from dashboard import subscription_commands as sc
        pending = {
            "intent": "edit_template", "template_key": "myreport",
            "template_name": "自定义周报",
            "section_spec": [{"kind": "headline", "title": "总体结论"},
                             {"kind": "claims", "title": "各表进展"}],
        }
        text = sc.render_confirmation(pending)
        self.assertIn("自定义周报", text)
        self.assertIn("总体结论 / 各表进展", text)
        self.assertIn("覆盖保存", text)
        self.assertIn("取消", text)


class DashboardSkillTemplateTests(unittest.TestCase):
    """技能层：模板列表/切换/描述/提交（mock 存储，不打真实库/API）"""

    def setUp(self):
        from dashboard.template_store import TemplateStore
        self._tpl_path = _tmp_db()
        self._tpl_store = TemplateStore(db_path=self._tpl_path)
        self.patch_tpl = mock.patch(
            "dashboard.template_store.get_template_store",
            return_value=self._tpl_store)
        self.patch_tpl.start()
        self.addCleanup(self.patch_tpl.stop)
        self.addCleanup(lambda: _cleanup_db(self._tpl_path, self._tpl_store))

        self._sub_path = _tmp_db()
        self._sub_store = SubscriptionStore(db_path=self._sub_path)
        self.patch_sub = mock.patch(
            "skills.dashboard.get_subscription_store",
            return_value=self._sub_store)
        self.patch_sub.start()
        self.addCleanup(self.patch_sub.stop)
        self.addCleanup(lambda: _cleanup_db(self._sub_path, self._sub_store))

        self.patch_pending = mock.patch(
            "pending_context._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)

        self._create_sub("union001")

    def _create_sub(self, uid):
        sub = Subscription(
            owner_user_id=uid, owner_staff_id="staff001", owner_union_id=uid,
            data_sources=["project_status"], push_hour=9, push_minute=0,
            weekdays="", alert_mode="always", recipients=["staff001"],
            title="恩特能源每日项目看板",
        )
        return self._sub_store.create(sub)

    def _confirm(self, uid="union001"):
        from dashboard import subscription_commands as sc
        pending = sc.get_pending(uid)
        self.assertIsNotNone(pending, "应有待确认操作")
        return pending

    def test_template_list_answer(self):
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle("看板模板", user_id="union001")
        self.assertEqual(r["source"], "dashboard")
        self.assertIn("每日简报", r["answer"])
        self.assertIn("周报总结", r["answer"])
        self.assertIn("项目看板", r["answer"])

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_set_template_confirm_flow(self, m_push):
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle("用周报模板做看板", user_id="union001")
        self.assertIn("周报总结", r["answer"])
        self.assertEqual(self._confirm()["intent"], "set_template")
        r2 = DashboardSkill.handle("确认", user_id="union001")
        self.assertIn("切换为", r2["answer"])
        # 必须读 mock 的临时库（self._sub_store）——真实 get_subscription_store
        # 单例连生产库 data/user_store.db，会被既有订阅污染
        sub = self._sub_store.list_for_owner("union001")[0]
        self.assertEqual(sub.template_id, "weekly")

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    @mock.patch("dashboard.template_builder.describe_to_spec")
    def test_describe_template_confirm_flow(self, m_describe, m_push):
        m_describe.return_value = {
            "ok": True, "name": "晨会看板",
            "description": "晨会",
            "map_instructions": "优先今日变化",
            "reduce_instructions": "按板块组织",
            "section_spec": [
                {"kind": "headline", "title": "今日要点"},
                {"kind": "claims", "title": "进展",
                 "levels": ["update", "info"]},
                {"kind": "claims", "title": "风险",
                 "levels": ["risk", "decision"]},
                {"kind": "sources", "title": "数据来源"},
            ],
        }
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle(
            "按这个格式做看板：负责人/今日进展/明日计划", user_id="union001")
        self.assertIn("晨会看板", r["answer"])
        pending = self._confirm()
        self.assertEqual(pending["intent"], "describe_template")
        r2 = DashboardSkill.handle("确认", user_id="union001")
        self.assertIn("已保存模板", r2["answer"])
        sub = self._sub_store.list_for_owner("union001")[0]
        self.assertEqual(sub.template_id, "晨会看板")
        self.assertIsNotNone(self._tpl_store.get("晨会看板", "union001"))

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    @mock.patch("dashboard.template_builder.parse_template_file")
    @mock.patch("knowledge_review.get_pending_learn")
    def test_submit_template_confirm_flow(self, m_learn, m_parse, m_push):
        m_learn.return_value = {"file_path": "/tmp/周报模板.md",
                                "file_name": "周报模板.md"}
        m_parse.return_value = {
            "ok": True, "name": "周报模板",
            "description": "从文件识别",
            "map_instructions": "优先今日变化",
            "reduce_instructions": "按章节组织",
            "section_spec": [
                {"kind": "headline", "title": "本周要点"},
                {"kind": "claims", "title": "进展",
                 "levels": ["update", "info"]},
                {"kind": "sources", "title": "数据来源"},
            ],
        }
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle("把这个当看板模板", user_id="union001")
        self.assertIn("周报模板", r["answer"])
        pending = self._confirm()
        self.assertEqual(pending["intent"], "submit_template")
        r2 = DashboardSkill.handle("确认", user_id="union001")
        self.assertIn("已保存模板", r2["answer"])
        sub = self._sub_store.list_for_owner("union001")[0]
        self.assertEqual(sub.template_id, "周报模板")

    def test_submit_without_uploaded_file_hints(self):
        from skills.dashboard import DashboardSkill
        with mock.patch("knowledge_review.get_pending_learn",
                        return_value=None):
            r = DashboardSkill.handle("把这个当看板模板", user_id="union001")
        self.assertIn("先上传", r["answer"])

    def _create_user_template(self, uid="union001", key="myreport",
                              name="自定义周报"):
        res = self._tpl_store.create_user_template(
            key=key, name=name, user_id=uid,
            description="旧描述", map_instructions="旧指令",
            reduce_instructions="旧reduce",
            section_spec=[{"kind": "headline", "title": "旧章节"}])
        self.assertTrue(res["ok"])
        return res["template"]

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    @mock.patch("dashboard.template_builder.describe_to_spec")
    def test_edit_template_confirm_flow(self, m_describe, m_push):
        """v1.12.1：编辑私有模板 → 确认 → 覆盖字段、key 不变、无副本"""
        self._create_user_template()
        m_describe.return_value = {
            "ok": True, "name": "自定义周报",  # 编辑不换名，用原 key/name
            "description": "新描述：先结论后分块",
            "map_instructions": "只看变化",
            "reduce_instructions": "先总体后各表",
            "section_spec": [
                {"kind": "headline", "title": "总体结论"},
                {"kind": "claims", "title": "各表进展", "levels": ["update"]},
                {"kind": "sources", "title": "数据来源"},
            ],
        }
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle(
            "编辑自定义周报改成：先写总体结论", user_id="union001")
        self.assertIn("自定义周报", r["answer"])
        self.assertIn("总体结论", r["answer"])  # render_confirmation 展示新结构
        pending = self._confirm()
        self.assertEqual(pending["intent"], "edit_template")
        self.assertEqual(pending["template_key"], "myreport")
        # 确认 → 覆盖保存
        r2 = DashboardSkill.handle("确认", user_id="union001")
        self.assertIn("已按新格式覆盖", r2["answer"])
        m_push.assert_called_once()
        updated = self._tpl_store.get("myreport", "union001")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.description, "新描述：先结论后分块")
        self.assertEqual(updated.section_spec[0]["title"], "总体结论")
        # 无副本（同名未新建）
        mine = [t for t in self._tpl_store.list_visible("union001")
                if t.scope == "user"]
        self.assertEqual(len(mine), 1)

    @mock.patch("dashboard.template_builder.describe_to_spec")
    def test_edit_no_description_shows_current(self, m_describe):
        """v1.12.1：无格式描述 → 展示当前内容引导，不 set pending"""
        self._create_user_template()
        from skills.dashboard import DashboardSkill
        from dashboard import subscription_commands as sc
        m_describe.assert_not_called()
        r = DashboardSkill.handle("编辑自定义周报模板", user_id="union001")
        self.assertIn("当前结构", r["answer"])
        self.assertIn("自定义周报", r["answer"])
        self.assertIn("改成：", r["answer"])
        self.assertIsNone(sc.get_pending("union001"))

    def test_edit_system_template_rejected(self):
        """v1.12.1：系统模板不可编辑 → 引导另建"""
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle("编辑周报模板改成：先写结论", user_id="union001")
        self.assertIn("系统自带模板", r["answer"])
        self.assertIn("周报总结", r["answer"])

    def test_edit_unknown_template_guides(self):
        """v1.12.1：找不到模板 → 引导说「看板模板」查看"""
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle("编辑不存在的模板改成：先写结论", user_id="union001")
        self.assertIn("没找到", r["answer"])
        self.assertIn("看板模板", r["answer"])

    def test_subscription_status_shows_template(self):
        from skills.dashboard import DashboardSkill
        r = DashboardSkill.handle("我的看板", user_id="union001")
        self.assertIn("每日简报", r["answer"])
        self.assertIn("模板", r["answer"])


class TemplateChoiceFlowTests(unittest.TestCase):
    """创建订阅后主动反问选模板（v1.12.0）"""

    def setUp(self):
        from dashboard.template_store import TemplateStore
        self._tpl_path = _tmp_db()
        self._tpl_store = TemplateStore(db_path=self._tpl_path)
        self.patch_tpl = mock.patch(
            "dashboard.template_store.get_template_store",
            return_value=self._tpl_store)
        self.patch_tpl.start()
        self.addCleanup(self.patch_tpl.stop)
        self.addCleanup(lambda: _cleanup_db(self._tpl_path, self._tpl_store))

        self._sub_path = _tmp_db()
        self._sub_store = SubscriptionStore(db_path=self._sub_path)
        self.patch_sub = mock.patch(
            "skills.dashboard.get_subscription_store",
            return_value=self._sub_store)
        self.patch_sub.start()
        self.addCleanup(self.patch_sub.stop)
        self.addCleanup(lambda: _cleanup_db(self._sub_path, self._sub_store))

        self.patch_pending = mock.patch(
            "pending_context._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)

    def _create_and_ask(self, uid="union001"):
        """完整创建流程：帮推看板 → 确认 → 返回反问文案 + 已设 choose_template pending"""
        from skills.dashboard import DashboardSkill
        with mock.patch("skills.dashboard.DashboardSkill._staff_id_of",
                        return_value="staff001"):
            DashboardSkill.handle("帮我推个看板", user_id=uid)
        with mock.patch("skills.dashboard.DashboardSkill._push_sample",
                        return_value="已推送示例看板"):
            return DashboardSkill.handle("确认", user_id=uid)

    def test_parse_choice_without_pending_returns_none(self):
        from dashboard import subscription_commands as sc
        for t in ("1", "2", "3", "周报", "不用了", "项目"):
            self.assertIsNone(sc.parse_template_choice(t, "union001"), t)

    def test_parse_choice_number_name_none(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("union001", {"intent": "choose_template", "sub_id": 1})
        cases = [
            ("1", "daily"), ("2", "weekly"), ("3", "project"),
            ("周报", "weekly"), ("每日简报", "daily"), ("项目看板", "project"),
            ("项目", "project"), ("不用了", "none"), ("就这样", "none"),
            ("2周报", "weekly"),
        ]
        for t, expect in cases:
            p = sc.parse_template_choice(t, "union001")
            self.assertIsNotNone(p, t)
            self.assertEqual(p["choice"], expect, t)

    def test_parse_choice_requires_recent_window(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("union001", {"intent": "choose_template", "sub_id": 1})
        sc._activity_by_user["union001"] = 0.0  # 反问窗口过期
        self.assertIsNone(sc.parse_template_choice("2", "union001"))

    def test_parse_choice_does_not_steal_queries(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("union001", {"intent": "choose_template", "sub_id": 1})
        for t in ("项目进展怎么样", "帮我查下周报数据", "看板今天数据如何"):
            self.assertIsNone(sc.parse_template_choice(t, "union001"), t)

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_create_asks_template_choice(self, m_push):
        from dashboard import subscription_commands as sc
        from skills.dashboard import DashboardSkill
        with mock.patch("skills.dashboard.DashboardSkill._staff_id_of",
                        return_value="staff001"):
            DashboardSkill.handle("帮我推个看板", user_id="union001")
        r = DashboardSkill.handle("确认", user_id="union001")
        self.assertIn("1 每日简报", r["answer"])
        self.assertIn("2 周报总结", r["answer"])
        self.assertIn("3 项目看板", r["answer"])
        self.assertEqual(sc.get_pending("union001")["intent"], "choose_template")

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_choose_weekly_applies_to_subscription(self, m_push):
        from skills.dashboard import DashboardSkill
        self._create_and_ask("union001")
        r = DashboardSkill.handle("2", user_id="union001")
        self.assertIn("周报总结", r["answer"])
        self.assertEqual(self._sub_store.list_for_owner("union001")[0].template_id,
                         "weekly")

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_choose_daily_by_name(self, m_push):
        from skills.dashboard import DashboardSkill
        self._create_and_ask("union001")
        r = DashboardSkill.handle("每日简报", user_id="union001")
        self.assertIn("每日简报", r["answer"])
        self.assertEqual(self._sub_store.list_for_owner("union001")[0].template_id,
                         "daily")

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_choose_none_keeps_daily_and_clears(self, m_push):
        from dashboard import subscription_commands as sc
        from skills.dashboard import DashboardSkill
        self._create_and_ask("union001")
        r = DashboardSkill.handle("不用了", user_id="union001")
        self.assertIn("保持当前模板", r["answer"])
        self.assertEqual(self._sub_store.list_for_owner("union001")[0].template_id,
                         "daily")
        self.assertIsNone(sc.get_pending("union001"))

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_confirm_during_choice_keeps_default(self, m_push):
        from dashboard import subscription_commands as sc
        from skills.dashboard import DashboardSkill
        self._create_and_ask("union001")
        r = DashboardSkill.handle("好的", user_id="union001")
        self.assertIn("保持当前模板", r["answer"])
        self.assertEqual(self._sub_store.list_for_owner("union001")[0].template_id,
                         "daily")
        self.assertIsNone(sc.get_pending("union001"))

    @mock.patch("skills.dashboard.DashboardSkill._push_sample",
                return_value="已推送示例看板")
    def test_choose_template_pushes_sample_again(self, m_push):
        from skills.dashboard import DashboardSkill
        self._create_and_ask("union001")
        DashboardSkill.handle("2", user_id="union001")
        self.assertGreaterEqual(m_push.call_count, 1)


if __name__ == "__main__":
    unittest.main()
