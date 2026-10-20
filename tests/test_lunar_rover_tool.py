"""planetary_rover_location_map の月面ローバー経路と
人工物着陸地点重ね表示の統合テスト（ネットワークを使わない部分のみ）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from space_finder_mcp import planetary_rover as pr  # noqa: E402


class TestLunarRoverDispatch(unittest.TestCase):
    """月面ローバーの振り分けとエラー処理。"""

    def test_moon_is_known_body(self):
        self.assertIn("moon", pr.ROVER_BODIES)
        self.assertEqual(pr.ROVER_BODIES["moon"]["ja"], "月")

    def test_lunar_rover_keys(self):
        for k in ("apollo11", "apollo12", "apollo14", "apollo15", "apollo16", "apollo17",
                  "lunokhod1", "lunokhod2", "yutu", "yutu2"):
            self.assertIn(k, pr.LUNAR_ROVER_KEYS)

    def test_body_mismatch_is_rejected(self):
        """月のローバーを火星で指定したらエラー。"""
        r = pr.planetary_rover_location_map(body="mars", rover="apollo15")
        self.assertEqual(r.structuredContent["error"], "body mismatch")

    def test_mars_rover_on_moon_is_rejected(self):
        r = pr.planetary_rover_location_map(body="moon", rover="perseverance")
        self.assertEqual(r.structuredContent["error"], "body mismatch")

    def test_unknown_rover(self):
        r = pr.planetary_rover_location_map(body="moon", rover="no-such-rover")
        self.assertEqual(r.structuredContent["error"], "unsupported rover")
        self.assertIn("apollo15", r.structuredContent["supported"])

    def test_unknown_body(self):
        r = pr.planetary_rover_location_map(body="pluto", rover="apollo15")
        self.assertEqual(r.structuredContent["error"], "unknown body")
        self.assertIn("moon", r.structuredContent["known"])


class TestMarsOtherSitesOverlay(unittest.TestCase):
    """同じ天体にあるほかの人工物着陸地点の重ね表示。"""

    def test_show_sites_param_exists(self):
        import inspect
        sig = inspect.signature(pr.planetary_rover_location_map)
        self.assertIn("show_sites", sig.parameters)
        self.assertTrue(sig.parameters["show_sites"].default)

    def test_crater_and_impact_excluded(self):
        """自然衝突クレーターと人工物衝突は「着陸地点」ではないので重ねない。"""
        from space_finder_mcp.planetary_map import SITES
        moon = SITES["moon"]
        self.assertEqual(moon["mcgetchin"]["kind"], "crater")
        self.assertEqual(moon["falcon9"]["kind"], "impact")
        # 通常の着陸地点には kind が無い（= 描いてよい）
        self.assertNotIn("kind", moon["apollo15"])
        self.assertNotIn("kind", moon["surveyor3"])


if __name__ == "__main__":
    unittest.main()
