"""速度改善（ベクトル化・並列取得・タイルぼかし・遮断記憶）の回帰テスト（ネットワーク不要）。

直したのは「速さ」だが、壊してはいけないのは「値と画素」。ここでは
1) 日食の幾何計算が1点ずつ計算した値と一致する
2) TLE の並列取得が直列取得と同じ対応表を返す（1件の失敗も落とさない）
3) マーカーの光暈が全画面ブラーと同じ画素になる
4) 遮断状態がプロセスをまたいで残り、クールダウンが切れたら消える
を固定する。
"""
import math
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, "src")


def _scalar_geom(site, t, sun, moon):
    """ベクトル化前の実装（1点ずつ計算）。比較用の基準としてそのまま残す。"""
    s = site.at(t).observe(sun).apparent()
    m = site.at(t).observe(moon).apparent()
    sep = s.separation_from(m).degrees
    rs = math.degrees(math.asin(696000.0 / s.distance().km))
    rm = math.degrees(math.asin(1737.4 / m.distance().km))
    s_ra = s.radec()[0]
    s_dec = s.radec()[1]
    m_ra = m.radec()[0]
    m_dec = m.radec()[1]
    dra = math.radians((m_ra.hours - s_ra.hours) * 15.0)
    sd = math.radians(s_dec.degrees)
    md = math.radians(m_dec.degrees)
    pa = math.atan2(math.sin(dra), math.cos(sd) * math.tan(md) - math.sin(sd) * math.cos(dra))
    return rs, rm, sep, math.degrees(pa) % 360


class EclipseVectorizationTests(unittest.TestCase):
    def test_geom_many_matches_scalar_math(self):
        from space_finder_mcp import solar_eclipse as se

        loader, eph = se._load()
        ts = loader.timescale()
        from skyfield.api import wgs84
        site = eph["earth"] + wgs84.latlon(35.68, 139.77)
        sun, moon = eph["sun"], eph["moon"]
        jds = [2460600.5, 2460600.6, 2460600.7]
        tt = ts.tt_jd(jds)
        rs, rm, sep, pa = se._geom_many(site, tt, sun, moon)
        self.assertEqual(len(rs), 3)
        for i, jd in enumerate(jds):
            s_rs, s_rm, s_sep, s_pa = _scalar_geom(site, ts.tt_jd(jd), sun, moon)
            self.assertAlmostEqual(float(rs[i]), s_rs, places=9)
            self.assertAlmostEqual(float(rm[i]), s_rm, places=9)
            self.assertAlmostEqual(float(sep[i]), s_sep, places=9)
            self.assertAlmostEqual(float(pa[i]), s_pa, places=9)

    def test_scan_day_is_deterministic_offline(self):
        """探索は同じ日付で同じ結果を返す（並列化・ベクトル化で値が動いていないこと）。"""
        from space_finder_mcp import solar_eclipse as se

        loader, eph = se._load()
        ts = loader.timescale()
        from skyfield.api import wgs84
        site = eph["earth"] + wgs84.latlon(35.68, 139.77)
        a = se._scan_day(eph, ts, site, eph["sun"], eph["moon"], 2026, 10, 21, 9.0)
        b = se._scan_day(eph, ts, site, eph["sun"], eph["moon"], 2026, 10, 21, 9.0)
        self.assertEqual(a, b)


class ParallelTleTests(unittest.TestCase):
    def test_parallel_fetch_matches_serial_and_keeps_errors(self):
        from space_finder_mcp import sky_overlay as so

        calls = []

        def fake(catnr):
            calls.append(catnr)
            if catnr == 3:
                raise RuntimeError("boom")
            if catnr == 5:
                return (None, "")
            return ((("1 0000%d" % catnr), ("2 0000%d" % catnr)), "celestrak.org")

        with mock.patch.object(so, "_fetch_tle", side_effect=fake):
            got = so._fetch_tles_parallel([1, 2, 3, 5])

        self.assertEqual(sorted(calls), [1, 2, 3, 5])      # 全件を1回ずつ叩く
        self.assertEqual(got[1][1], "celestrak.org")
        self.assertEqual(got[3], (None, ""))               # 例外は「取得できず」に正規化
        self.assertEqual(got[5], (None, ""))               # 空もそのまま
        self.assertEqual(set(got), {1, 2, 3, 5})

    def test_parallel_fetch_does_not_double_fetch(self):
        from space_finder_mcp import sky_overlay as so

        calls = []

        def fake(catnr):
            calls.append(catnr)
            return ((("1", "2")), "celestrak.org")

        with mock.patch.object(so, "_fetch_tle", side_effect=fake):
            got = so._fetch_tles_parallel([7, 7, 9])
        self.assertEqual(sorted(calls), [7, 9])            # 重複 ID は1回
        self.assertEqual(set(got), {7, 9})


class GlowTileTests(unittest.TestCase):
    def test_tile_glow_equals_full_canvas_blur(self):
        """タイルでぼかした光暈が、全画面をぼかした結果と同じ画素になる。"""
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        from space_finder_mcp.solar_system import _glow

        W = H = 420
        color = (240, 190, 90)
        box = [200, 200, 240, 240]
        radius = 8

        # 旧実装（全画面 RGBA を作ってぼかす）
        old = Image.new("RGB", (W, H), (8, 10, 22))
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.ellipse([box[0] - 14, box[1] - 14, box[2] + 14, box[3] + 14], fill=color + (90,))
        halo = halo.filter(ImageFilter.GaussianBlur(radius))
        old.paste(Image.alpha_composite(old.convert("RGBA"), halo).convert("RGB"), (0, 0))

        # 新実装（周囲だけのタイル）
        new = Image.new("RGB", (W, H), (8, 10, 22))
        _glow(new, [box[0] - 14, box[1] - 14, box[2] + 14, box[3] + 14], radius, color + (90,))

        diff = ImageChops.difference(old, new)
        worst = max(max(px) for px in diff.get_flattened_data())
        self.assertEqual(worst, 0)

    def test_tile_glow_line_matches_full_canvas(self):
        """彗星の尾（線分）でも同じ画素になる。"""
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        from space_finder_mcp.solar_system import _glow

        W = H = 300
        color = (120, 220, 255)
        pts = (100, 100, 170, 140)
        old = Image.new("RGB", (W, H), (8, 10, 22))
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.line(list(pts), fill=color + (140,), width=11)
        halo = halo.filter(ImageFilter.GaussianBlur(3))
        old.paste(Image.alpha_composite(old.convert("RGBA"), halo).convert("RGB"), (0, 0))

        new = Image.new("RGB", (W, H), (8, 10, 22))
        _glow(new, list(pts), 3, color + (140,), line_width=11)
        worst = max(max(px) for px in ImageChops.difference(old, new).get_flattened_data())
        self.assertEqual(worst, 0)


class BlockedStatePersistenceTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import celestrak
        self.c = celestrak
        self._tmp = tempfile.mkdtemp()
        self._old_file = celestrak._BLOCK_FILE
        self._old_loaded = celestrak._STATE_LOADED
        self._old_until = celestrak._COOLDOWN_UNTIL
        self._old_last = celestrak._COOLDOWN_LAST
        celestrak._BLOCK_FILE = os.path.join(self._tmp, "block.json")
        celestrak._STATE_LOADED = False
        celestrak._COOLDOWN_UNTIL = 0.0
        celestrak._COOLDOWN_LAST = ""

    def tearDown(self):
        self.c._BLOCK_FILE = self._old_file
        self.c._STATE_LOADED = self._old_loaded
        self.c._COOLDOWN_UNTIL = self._old_until
        self.c._COOLDOWN_LAST = self._old_last
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_block_survives_new_process(self):
        self.c._note_blocked(60.0, "接続できません")
        self.assertTrue(os.path.exists(self.c._BLOCK_FILE))
        # 新しいプロセスを模す（メモリの状態を捨てる）
        self.c._STATE_LOADED = False
        self.c._COOLDOWN_UNTIL = 0.0
        self.c._COOLDOWN_LAST = ""
        remaining, reason = self.c.blocked_status()
        self.assertGreater(remaining, 0.0)
        self.assertIn("接続できません", reason)

    def test_expired_block_is_ignored(self):
        self.c._note_blocked(-5.0, "古い遮断")     # 期限切れの記憶
        self.c._STATE_LOADED = False
        self.c._COOLDOWN_UNTIL = 0.0
        remaining, _reason = self.c.blocked_status()
        self.assertEqual(remaining, 0.0)      # 期限切れは遮断として扱わない

    def test_corrupt_file_is_ignored(self):
        with open(self.c._BLOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(self.c.blocked_status(), (0.0, ""))


if __name__ == "__main__":
    unittest.main()
