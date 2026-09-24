"""新規データ源（CDS SIMBAD/VizieR・NASA Exoplanet Archive・みちびき QZSS）の回帰テスト（ネットワーク不要）。

再発防止する実バグ:
- SP3 のヘッダ（`#cP…ORBIT…`）からエポック数を取ろうとして列位置が製品ごとにずれ、
  全機 0 機になって「解析失敗」を返した（実測）。エポック数はヘッダから取らず、
  最初の `*`（エポック）行より後ろの P 行だけを読む。
- 和名を SIMBAD の識別子表へそのまま投げて HTTP 400 になり、天体名検索が丸ごと失敗した（実測）。
  ASCII 名へ展開してから引く。
- Exoplanet Archive の hostname は "Kepler-22" 形式なので、"ケプラー22" をそのまま投げると 0 件。
  番号を連結した形（kepler22 / kepler-22）まで展開する。
"""
import sys
import unittest

sys.path.insert(0, "src")


def _pline(sat, x, y, z):
    """SP3 の P 行を組み立てる（列位置が本体と同じになるように桁を固定する）。"""
    return "P{}{:14.6f}{:14.6f}{:14.6f}".format(sat, x, y, z)


SP3_TEXT = "\n".join([
    "#cP2026  9 22 18  0  0.00000000     2 ORBIT   JGS FIT  QSS",
    "## 2437 237600.00000000   900.00000000 61305 0.7500000000000",
    "+    2   G01J07",
    # ヘッダに紛れた P 行（エポック行より前は読んではいけない）
    _pline("J07", -1.0, -1.0, -1.0),
    "*  2026  9 22 18  0  0.00000000",
    _pline("G01", -14796.063586, 4431.938295, 21359.161785),
    _pline("J07", -25395.234792, 33664.478246, -113.403156),
    "*  2026  9 22 18 15  0.00000000",
    _pline("G01", -13873.427251, 4247.287454, 22343.450053),
    _pline("J07", -25408.004711, 33655.338692, -113.209750),
]) + "\n"


class Sp3Tests(unittest.TestCase):
    def test_epoch_major_parse(self):
        from space_finder_mcp import qzss
        p = qzss._parse_sp3(SP3_TEXT)
        self.assertEqual(len(p["epochs"]), 2)
        self.assertEqual(sorted(p["sats"]), ["G01", "J07"])
        # 2 エポック分が衛星ごとに揃い、ヘッダの P 行は含まれない
        self.assertEqual(len(p["sats"]["J07"]), 2)
        self.assertAlmostEqual(p["sats"]["J07"][0][0], -25395.234792, places=4)
        self.assertAlmostEqual(p["sats"]["J07"][1][2], -113.209750, places=4)
        self.assertEqual((p["epochs"][1] - p["epochs"][0]).total_seconds(), 900.0)

    def test_garbage_is_empty(self):
        from space_finder_mcp import qzss
        p = qzss._parse_sp3("not an sp3 file\n")
        self.assertEqual(p["epochs"], [])
        self.assertEqual(p["sats"], {})


class GeometryTests(unittest.TestCase):
    def test_geodetic_round_trip(self):
        from space_finder_mcp import qzss
        lat, lon, alt = 35.68, 139.69, 0.5
        a, e2 = qzss._A, qzss._E2
        import math
        slat, clat = math.sin(math.radians(lat)), math.cos(math.radians(lat))
        n = a / math.sqrt(1.0 - e2 * slat * slat)
        x = (n + alt) * clat * math.cos(math.radians(lon))
        y = (n + alt) * clat * math.sin(math.radians(lon))
        z = (n * (1.0 - e2) + alt) * slat
        rlat, rlon, ralt = qzss._ecef_to_geodetic(x, y, z)
        self.assertAlmostEqual(rlat, lat, places=6)
        self.assertAlmostEqual(rlon, lon, places=6)
        self.assertAlmostEqual(ralt, alt, places=3)

    def test_look_angles(self):
        from space_finder_mcp import qzss
        # 観測点の真上（赤道・経度0）と真下
        el, az, rng = qzss._look_angles(qzss._A + 35786.0, 0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(el, 90.0, places=6)
        self.assertAlmostEqual(rng, 35786.0, places=3)
        el2, _az2, _r = qzss._look_angles(-(qzss._A + 35786.0), 0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(el2, -90.0, places=6)


class NameAndFormatTests(unittest.TestCase):
    def test_exoplanet_name_terms(self):
        from space_finder_mcp import exoplanet
        terms = [t.lower() for t in exoplanet._name_terms("ケプラー22")]
        self.assertIn("kepler22", terms)
        self.assertIn("kepler-22", terms)
        self.assertIn("trappist-1", [t.lower() for t in exoplanet._name_terms("TRAPPIST-1")])

    def test_simbad_terms_are_ascii(self):
        from space_finder_mcp import cds, name_common
        # 和名は ASCII の英語名へ展開される（SIMBAD の識別子表は ASCII のみ）
        terms = [t for t in name_common.expand_terms("シリウス") if t.isascii()]
        self.assertTrue(terms)
        self.assertIn("sirius", [t.lower() for t in terms])

    def test_lit_strips_quotes(self):
        from space_finder_mcp import cds, exoplanet
        # クォート（注入）と % （LIKE ワイルドカード）はどちらも落ちる
        for f in (cds._lit, exoplanet._lit):
            self.assertEqual(f("a'b%c"), "'abc'")
            self.assertEqual(f("O'Brien"), "'OBrien'")

    def test_sexagesimal(self):
        from space_finder_mcp import cds
        self.assertEqual(cds._hms(15.0), "01h00m00.0s")
        self.assertEqual(cds._dms(-30.5), "-30d30m00.0s")

    def test_sep_arcsec(self):
        from space_finder_mcp import cds
        self.assertAlmostEqual(cds._sep_arcsec(0.0, 0.0, 0.0, 1.0), 3600.0, places=3)

    def test_qzss_prn_mapping_has_geo_entries(self):
        from space_finder_mcp import qzss
        self.assertEqual(qzss.QZSS_SATS["J07"][1], "みちびき3号")
        self.assertTrue(qzss.QZSS_SATS["J07"][3].startswith("静止"))
        self.assertEqual(qzss.QZSS_SATS["J04"][1], "みちびき1号後継機")


class NoNetworkCandidateTests(unittest.TestCase):
    """未知の入力では推測せず候補を返して停止する（ネットワークに触れない）。"""

    def test_qzss_unknown_kind(self):
        from space_finder_mcp import qzss
        r = qzss.qzss_status(kind="zzz")
        self.assertEqual(r.structuredContent["count"], 0)
        self.assertEqual(len(r.structuredContent["candidates"]), len(qzss.PRODUCTS))

    def test_vizier_unknown_catalog(self):
        from space_finder_mcp import cds
        r = cds.catalog_search(object_name="M31", catalog="nope")
        sc = r.structuredContent
        self.assertEqual(sc["count"], 0)
        self.assertEqual(len(sc["candidates"]), len(cds.CATALOGS))


if __name__ == "__main__":
    unittest.main()
