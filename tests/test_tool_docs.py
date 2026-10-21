"""tools/list の軽量化（description の圧縮・inputSchema の無駄取り）の回帰テスト。

守るのは2点:
1. **落としてはいけない情報が残る** — 要約・例・Args（引数の一次情報）・契約文
   （メディアのリンク提示／figure.notes の引用）は必ず残る。
2. **元の docstring は書き換えない** — リポジトリを読む人は従来どおり完全な説明を読める。
"""
import sys
import unittest

sys.path.insert(0, "src")

from space_finder_mcp.tool_docs import compact_description, slim_schema

DOC = '''太陽を中心とした太陽系の惑星の現在位置図を返す（認証不要）。

    例:「太陽系を上から見た図」「今の惑星の位置」

    ここは背景説明の長い段落で、落としてよい。**1光日**の説明もここに書いてある。
    これは2段落目以降なので削られる対象。

    Args:
        when: 時刻 ISO8601。省略時は現在時刻。
        engine: "simple"(既定) / "accurate"。

    ⚠️ figure.notes は図の誤読を防ぐための注記なので、要約・言い換えせずそのまま引用すること。
    回答時はこのリンクをそのまま提示してください。
'''


class CompactDescriptionTests(unittest.TestCase):
    def test_keeps_summary_examples_args_and_contract(self):
        got = compact_description(DOC)
        self.assertIn("現在位置図を返す", got)             # 要約
        self.assertIn("例:「太陽系を上から見た図」", got)     # 例
        self.assertIn("Args:", got)                        # 引数の一次情報
        self.assertIn('engine: "simple"(既定) / "accurate"。', got)  # Args は改変しない
        self.assertIn("要約・言い換えせずそのまま引用すること", got)  # 契約
        self.assertIn("そのまま提示してください", got)        # 契約
        self.assertNotIn("背景説明の長い段落", got)           # 背景説明は落とす

    def test_args_block_is_not_truncated(self):
        """Args を機械的に切ると括弧が閉じない文になる（実測）。一字も変えないこと。"""
        doc = '概要。\n\n    Args:\n        a: 長い説明（"x"/"y"/"z" を受け付け、\n            続きの行も意味がある。\n'
        got = compact_description(doc)
        self.assertIn('続きの行も意味がある。', got)

    def test_short_docstring_is_unchanged(self):
        self.assertEqual(compact_description("短い説明だけ。"), "短い説明だけ。")

    def test_empty_docstring(self):
        self.assertEqual(compact_description(""), "")
        self.assertEqual(compact_description(None), "")

    def test_reduces_length(self):
        self.assertLess(len(compact_description(DOC)), len(DOC))

    def test_original_docstring_is_not_mutated(self):
        before = DOC
        compact_description(DOC)
        self.assertEqual(DOC, before)


class SlimSchemaTests(unittest.TestCase):
    def test_removes_title_and_folds_null_union(self):
        schema = {"type": "object", "properties": {
            "a": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                  "title": "a"},
            "b": {"default": "calendar", "title": "b", "type": "string"}},
            "required": []}
        got = slim_schema(schema)
        self.assertEqual(got["properties"]["a"], {"type": ["string", "null"]})
        self.assertEqual(got["properties"]["b"], {"default": "calendar", "type": "string"})
        self.assertNotIn("title", str(got))

    def test_keeps_meaningful_union(self):
        """number|string のような本物の union は畳まない。"""
        schema = {"properties": {"x": {"anyOf": [{"type": "number"}, {"type": "string"}],
                                       "default": 0}}}
        got = slim_schema(schema)
        self.assertEqual(got["properties"]["x"]["anyOf"],
                         [{"type": "number"}, {"type": "string"}])
        self.assertEqual(got["properties"]["x"]["default"], 0)

    def test_keeps_required_and_other_keys(self):
        schema = {"type": "object", "required": ["a"], "additionalProperties": False,
                  "properties": {"a": {"type": "string"}}}
        got = slim_schema(schema)
        self.assertEqual(got["required"], ["a"])
        self.assertIs(got["additionalProperties"], False)


class RegisteredToolsAreCompactTests(unittest.TestCase):
    def test_registered_tools_keep_args_and_contract(self):
        """実際に登録されたツールの description が「要約＋Args＋契約文」を保っていること。"""
        from space_finder_mcp import server  # noqa: F401  （登録の副作用が目的）

        mcp = server.mcp
        tools = {t.name: t for t in mcp._tool_manager.list_tools()}
        self.assertGreaterEqual(len(tools), 60)
        # 画像を返すツールは「リンクをそのまま提示」の契約文を description に残す
        desc = tools["moon_phase_map"].description
        self.assertIn("そのまま提示してください", desc)
        self.assertIn("Args:", desc)
        # 全体として元の docstring より短い
        fn = server._moon_phase.moon_phase_map
        self.assertLess(len(desc), len(fn.__doc__))


if __name__ == "__main__":
    unittest.main()
