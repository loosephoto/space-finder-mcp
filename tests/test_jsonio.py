"""JSON 受け渡しの契約（`jsonio.to_json` / `server._delivered`）の回帰テスト。

発端（実測 2026-11-01）: Skyfield の戻り値をそのまま `structuredContent` に載せていたため、
`constellation_now` は 45 個、`solar_system_now` は 9 個の `numpy.float64` を含んでいた。
JSON の7型だけで構成されることを、ここで固定する。
"""
import datetime
import decimal
import sys
import unittest

sys.path.insert(0, "src")

from mcp.types import CallToolResult, TextContent

from space_finder_mcp import jsonio, server


class ToJsonTests(unittest.TestCase):
    def test_numpy_scalars_become_python(self):
        import numpy as np

        out = jsonio.to_json({"a": np.float64(1.5), "b": np.int64(3), "c": np.str_("x")})
        self.assertEqual(out, {"a": 1.5, "b": 3, "c": "x"})
        self.assertIs(type(out["a"]), float)
        self.assertIs(type(out["b"]), int)
        self.assertIs(type(out["c"]), str)

    def test_numpy_array_becomes_list(self):
        import numpy as np

        out = jsonio.to_json(np.array([[1.0, 2.0], [3.0, 4.0]]))
        self.assertEqual(out, [[1.0, 2.0], [3.0, 4.0]])
        self.assertTrue(jsonio.is_json_native(out))

    def test_nan_and_inf_become_null(self):
        out = jsonio.to_json({"nan": float("nan"), "inf": float("inf"), "ok": 1.5})
        self.assertIsNone(out["nan"])
        self.assertIsNone(out["inf"])
        self.assertEqual(out["ok"], 1.5)

    def test_datetime_and_decimal(self):
        out = jsonio.to_json({"t": datetime.datetime(2026, 11, 1, 3, 4, 5),
                              "d": datetime.date(2026, 11, 1),
                              "n": decimal.Decimal("2.50"),
                              "i": decimal.Decimal("2")})
        self.assertEqual(out["t"], "2026-11-01T03:04:05")
        self.assertEqual(out["d"], "2026-11-01")
        self.assertEqual(out["n"], 2.5)
        self.assertIs(type(out["i"]), int)

    def test_set_is_sorted_and_tuple_is_list(self):
        self.assertEqual(jsonio.to_json({"b", "a"}), ["a", "b"])
        self.assertEqual(jsonio.to_json((1, 2)), [1, 2])

    def test_dict_keys_become_strings(self):
        self.assertEqual(jsonio.to_json({1: "x"}), {"1": "x"})

    def test_is_json_native_rejects_numpy_and_nan(self):
        import numpy as np

        self.assertFalse(jsonio.is_json_native({"a": np.float64(1.0)}))
        self.assertFalse(jsonio.is_json_native({"a": float("nan")}))
        self.assertTrue(jsonio.is_json_native({"a": [1, "x", None, True]}))


class DeliveredTests(unittest.TestCase):
    """`server._delivered`（唯一の出口）が型を正規化し、入力を書き換えないこと。"""

    def test_structured_content_is_normalized(self):
        import numpy as np

        raw = CallToolResult(
            content=[TextContent(type="text", text="結果")],
            structuredContent={"au": np.float64(0.723), "arr": np.array([1.0, 2.0])},
        )
        out = server._delivered(raw)
        self.assertIs(type(out.structuredContent["au"]), float)
        self.assertEqual(out.structuredContent["arr"], [1.0, 2.0])
        # 呼び出し元（ttl_cache が共有し得る）の結果は書き換えない
        self.assertEqual(type(raw.structuredContent["au"]).__name__, "float64")

    def test_text_is_kept(self):
        raw = CallToolResult(content=[TextContent(type="text", text="本文")],
                             structuredContent={"a": 1})
        self.assertEqual(server._delivered(raw).content[0].text, "本文")


if __name__ == "__main__":
    unittest.main()
