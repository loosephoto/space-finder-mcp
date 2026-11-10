"""地名解決の共有化（`places.py`）と、太陽系の層分割の境界の回帰テスト。

- `places.resolve_place`: 既知テーブル・lat/lon の検証・未知の地名は None
- 共有テーブル: 同じ地名がツールごとに違う解決結果にならないこと
  （実測 2026-11-01: 「福岡」は `skyfield_pos` だけが解決できていた）
- 層分割: `solar_render` は通信しない / `solar_model` は描画しない
"""
import ast
import io
import os
import sys
import unittest

sys.path.insert(0, "src")

from space_finder_mcp import places

SRC = os.path.join("src", "space_finder_mcp")


def _imports(module):
    src = io.open(os.path.join(SRC, module), encoding="utf-8-sig").read()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[-1])
            names |= {a.name for a in node.names}
    return names


class ResolvePlaceTests(unittest.TestCase):
    def test_known_city(self):
        self.assertEqual(places.resolve_place("東京", None, None), (35.68, 139.69))
        self.assertEqual(places.resolve_place("Tokyo", None, None), (35.68, 139.69))
        self.assertEqual(places.resolve_place("  fukuoka ", None, None), (33.59, 130.40))

    def test_lat_lon_wins(self):
        self.assertEqual(places.resolve_place("東京", 10.0, 20.0), (10.0, 20.0))

    def test_lat_lon_are_clamped_by_as_float(self):
        """範囲外は `input_utils.as_float` が**クランプ**する（既存挙動・変更しない）。

        注意: リポジトリの規約「利用者向けの数値引数を黙ってクランプしない」と
        食い違う（lat=120 が 90 として通る）。ここでは移設前と同じ挙動であることを
        固定し、規約との不一致は別途の課題として扱う（この変更では意味を変えない）。
        """
        self.assertEqual(places.resolve_place(None, 120.0, 139.0), (90.0, 139.0))
        self.assertEqual(places.resolve_place(None, 35.0, 200.0), (35.0, 180.0))
        self.assertIsNone(places.resolve_place(None, "abc", 139.0))

    def test_unknown_place_offline_raises_resolver_unavailable(self):
        """ネットワーク断は「該当なし」と混同せず ResolverUnavailable で伝える（v0.57.0）。

        旧実装は None を返していたため、呼び出し側が東京で代用していた
        （solar_eclipse_series(place="パリ") が「パリ」と題した東京の図を返した）。
        ツール側はこれを error="resolver_unavailable" に変換する。
        """
        import requests
        from space_finder_mcp.name_common import ResolverUnavailable

        orig = requests.get

        def _boom(*a, **k):
            raise requests.RequestException("offline")

        requests.get = _boom
        try:
            with self.assertRaises(ResolverUnavailable):
                places.resolve_place("どこでもない場所", None, None)
        finally:
            requests.get = orig

    def test_unknown_place_when_resolver_answers_is_none(self):
        """サービスは応答したが該当なし → None（例外にしない）。"""
        import requests
        import space_finder_mcp.weather_astro as weather_astro

        class _Resp:
            def __init__(self, payload):
                self._p = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._p

        def _fake(url, *a, **k):
            return _Resp([] if "nominatim" in url else {"results": []})

        orig = requests.get
        orig_last = getattr(weather_astro._geocode_nominatim, "_last_call", 0.0)
        weather_astro._geocode_nominatim._last_call = 0.0
        requests.get = _fake
        try:
            self.assertIsNone(places.resolve_place("どこでもない場所", None, None))
        finally:
            requests.get = orig
            weather_astro._geocode_nominatim._last_call = orig_last

    def test_city_table_is_shared_by_tools(self):
        """同じ入力を別ツールが別結果にしない（表の複製を禁止する）。"""
        from space_finder_mcp import skyfield_pos, solar_eclipse

        for mod in (skyfield_pos, solar_eclipse):
            self.assertIs(getattr(mod, "_KNOWN_COORDS", places.CITY_COORDS),
                          places.CITY_COORDS, mod.__name__)


class SolarLayerSplitTests(unittest.TestCase):
    def test_render_does_not_touch_network(self):
        names = _imports("solar_render.py")
        self.assertNotIn("requests", names)
        self.assertNotIn("skyfield", names)

    def test_model_does_not_draw(self):
        names = _imports("solar_model.py")
        self.assertNotIn("PIL", names)
        self.assertNotIn("skyfield", names)

    def test_solar_system_reexports_moved_names(self):
        """既存の呼び出し元（comet_apparition・テスト）が壊れないこと。"""
        from space_finder_mcp import solar_system

        for name in ("_compute", "solar_system_now", "_render_oblique", "_verify_oblique",
                     "_sbdb_elements", "comet_xyz_from_elements", "norm_orbit_el",
                     "peri_times", "oblique_camera"):
            self.assertTrue(hasattr(solar_system, name), name)


if __name__ == "__main__":
    unittest.main()
