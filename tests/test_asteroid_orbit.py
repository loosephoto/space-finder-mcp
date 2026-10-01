import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "src")


class AsteroidOrbitTests(unittest.TestCase):
    def test_asteroid_orbit_view_returns_verified_ellipse_image(self):
        from space_finder_mcp import solar_system as ss

        raw = {
            "e": 0.2,
            "a": 1.2,
            "i": math.radians(6.0),
            "node": math.radians(2.0),
            "argp": math.radians(60.0),
            "epoch": 2460000.5,
            "ma": math.radians(10.0),
            "n": 0.8,
            "fullname": "101955 Bennu (1999 RQ36)",
            "kind": "an",
        }
        t = SimpleNamespace(tt=2460001.5, utc_strftime=lambda _fmt: "2023-02-25 00:00 UTC")
        loader = SimpleNamespace(timescale=lambda: object())

        with patch.object(ss, "_compute", side_effect=AssertionError("asteroid view fell through to system view")), \
             patch.object(ss, "_load", return_value=(loader, None)), \
             patch.object(ss, "_resolve_when", return_value=t), \
             patch.object(ss, "_sbdb_elements", return_value=raw):
            result = ss.solar_system_now(when="2023-02-25T00:00:00Z", asteroid="ベンヌ",
                                        view="asteroid_orbit")

        data = result.structuredContent or {}
        fig = data.get("figure") or {}
        conic = fig.get("conic") or {}
        verify = fig.get("verify") or {}
        image_path = data.get("image_path")
        try:
            self.assertNotIn("error", data)
            self.assertEqual(data.get("asteroid"), raw["fullname"])
            self.assertEqual(data.get("id"), "101955")
            self.assertEqual(fig.get("kind"), "orbit_plane")
            self.assertIn("小惑星自身", (fig.get("view") or {}).get("description", ""))
            self.assertTrue(any("小惑星" in note for note in fig.get("notes", [])))
            self.assertFalse(any("彗星自身" in note for note in fig.get("notes", [])))
            self.assertIn("epoch JD 2460000.50000", " ".join(fig.get("notes", [])))
            self.assertEqual(conic.get("kind"), "ellipse")
            self.assertAlmostEqual(conic.get("q"), raw["a"] * (1.0 - raw["e"]), places=6)
            self.assertAlmostEqual(conic.get("apo"), raw["a"] * (1.0 + raw["e"]), places=6)
            self.assertTrue(verify.get("ok"), verify)
            self.assertEqual(len(result.content), 2)
            self.assertEqual(result.content[1].type, "image")
            self.assertTrue(result.content[0].text.startswith("🖼️ "))
            self.assertTrue(image_path and Path(image_path).is_file())
        finally:
            if image_path:
                Path(image_path).unlink(missing_ok=True)

    def test_asteroid_orbit_view_requires_one_asteroid(self):
        from space_finder_mcp import solar_system as ss

        missing = ss.solar_system_now(view="asteroid_orbit")
        multiple = ss.solar_system_now(asteroid="ベンヌ", asteroid2="イトカワ",
                                       view="asteroid_orbit")
        self.assertIn("小惑星の指定が必要", missing.content[0].text)
        self.assertIn("error", missing.structuredContent or {})
        self.assertIn("1天体ずつ", multiple.content[0].text)
        self.assertEqual((multiple.structuredContent or {}).get("asteroids"), ["ベンヌ", "イトカワ"])

    def test_asteroid_orbit_view_rejects_open_conics(self):
        from space_finder_mcp import solar_system as ss

        t = SimpleNamespace(tt=2460001.5, utc_strftime=lambda _fmt: "2023-02-25 00:00 UTC")
        loader = SimpleNamespace(timescale=lambda: object())
        with patch.object(ss, "_load", return_value=(loader, None)), \
             patch.object(ss, "_resolve_when", return_value=t), \
             patch.object(ss, "_sbdb_elements", return_value={"a": -1.0, "e": 1.2}):
            result = ss.solar_system_now(asteroid="comet-like", view="asteroid_orbit")

        self.assertIn("error", result.structuredContent or {})
        self.assertIn("閉じた小惑星楕円", result.content[0].text)
        self.assertEqual(len(result.content), 1)


if __name__ == "__main__":
    unittest.main()
