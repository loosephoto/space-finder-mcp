"""weather_satellite_now（ひまわり9号の実画像取得）の回帰テスト。

ネットワークは使わない: requests.get を差し替え、ページの HTML と画像バイトを合成する。
重点は「配信元が実画像の代わりに置く全黒プレースホルダを弾くこと」と、
帯域名の解決（旧名の互換）、取得できなかったときに例外を漏らさないこと。
"""
import io
import sys
import unittest
from unittest import mock

sys.path.insert(0, "src")

import requests  # noqa: E402
from PIL import Image  # noqa: E402

from space_finder_mcp import weather_sat as ws  # noqa: E402


def _jpeg(w=601, h=601, grad=True):
    """実画像に近い（色数が多い）JPEG を作る。"""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(0, w, 1):
            if grad:
                px[x, y] = (x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _blank_png(w=550, h=550):
    """配信元のプレースホルダ（全黒・1bit PNG）。実測 444 バイト相当。"""
    im = Image.new("1", (w, h), 0)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _clear(fn):
    """ttl_cache のラッパーからキャッシュを消す（テスト間で時刻一覧を持ち越さない）。"""
    getattr(fn, "cache_clear", lambda: None)()


_PAGE = """<html><select name="slt_time">
<option value=2050>20:50 UTC 30 October 2026</option>
<option value=2040>20:40 UTC 30 October 2026</option>
<option value=2030>20:30 UTC 30 October 2026</option>
</select></html>"""


class Resp:
    def __init__(self, status=200, content=b"", text="", headers=None):
        self.status_code = status
        self.content = content
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("{} error".format(self.status_code))


class BandResolutionTests(unittest.TestCase):
    def test_supported_bands(self):
        for name in ("visible", "infrared", "water_vapor", "night_rgb", "day_rgb", "true_color"):
            key, spec = ws._resolve_band(name)
            self.assertEqual(key, name)
            self.assertTrue(spec["code"].startswith(("b", "n", "d")))
        # 赤外と夜間 RGB は夜でも使える（可視・自然色は昼向け）
        self.assertTrue(ws._HIMAWARI_BANDS["infrared"]["night_ok"])
        self.assertTrue(ws._HIMAWARI_BANDS["night_rgb"]["night_ok"])
        self.assertFalse(ws._HIMAWARI_BANDS["visible"]["night_ok"])

    def test_legacy_and_alias_codes(self):
        # 旧実装が使っていた "3d"/"8d" は帯域コードではなくタイル分割数だった（誤り）。
        # 互換のため旧値を現在の帯域へ読み替える。
        for raw, want in (("3d", "infrared"), ("8d", "true_color"), ("1d", "visible"),
                          ("ir", "infrared"), ("vis", "visible"), ("wv", "water_vapor"),
                          ("ngt", "night_rgb"), ("auto", "infrared")):
            key, _spec = ws._resolve_band(raw)
            self.assertEqual(key, want, raw)

    def test_unknown_band_is_none(self):
        self.assertEqual(ws._resolve_band("microwave")[0], None)


class ImageQualityTests(unittest.TestCase):
    def test_real_jpeg_passes(self):
        q = ws.image_quality(_jpeg())
        self.assertTrue(q["ok"], q)
        self.assertEqual(q["format"], "jpeg")
        self.assertEqual(q["size"], [601, 601])
        self.assertGreater(q["colors"], 500)

    def test_placeholder_png_is_rejected(self):
        """実測: 配信元は全黒 550×550 1bit の 444 バイト PNG を返すことがある。"""
        data = _blank_png()
        self.assertLess(len(data), 2000)          # 実測 444 バイト（環境により数百）
        q = ws.image_quality(data)
        self.assertFalse(q["ok"], q)
        self.assertTrue("小さすぎる" in q["why"] or "色数" in q["why"], q["why"])

    def test_non_image_bytes_are_rejected(self):
        q = ws.image_quality(b"<html>404 not found</html>")
        self.assertFalse(q["ok"])
        q2 = ws.image_quality(b"")
        self.assertFalse(q2["ok"])


class FetchTests(unittest.TestCase):
    def _get(self, mapping):
        def fake_get(url, **kwargs):
            for frag, resp in mapping:
                if frag in url:
                    return resp
            return Resp(404, b"", "")
        return fake_get

    def test_latest_frame_uses_jma_and_reports_utc(self):
        seen = []

        def fake_get(url, **kwargs):
            seen.append(url)
            if "sat_img.php" in url:
                return Resp(200, b"", _PAGE)
            if "fd__b13_2050" in url:
                return Resp(200, _jpeg(), "")
            return Resp(404, b"", "")

        with mock.patch.object(requests, "get", fake_get):
            _clear(ws._himawari_times)
            _clear(ws._latest_himawari_frame)
            obs, url, data, q = ws._latest_himawari_frame("b13")
        self.assertEqual(obs, "2026-10-30T20:50:00Z")      # ラベルの日付から作る（HHMM だけでは曖昧）
        self.assertIn("fd__b13_2050.jpg", url)
        self.assertTrue(q["ok"])
        self.assertTrue(any("jma.go.jp" in u for u in seen))

    def test_placeholder_frame_is_skipped(self):
        """最新スロットがプレースホルダなら、次の時刻の実画像を採る（黒い画像を返さない）。"""
        def fake_get(url, **kwargs):
            if "sat_img.php" in url:
                return Resp(200, b"", _PAGE)
            if "fd__b13_2050" in url:
                return Resp(200, _blank_png(), "")
            if "fd__b13_2040" in url:
                return Resp(200, _jpeg(), "")
            return Resp(404, b"", "")

        with mock.patch.object(requests, "get", fake_get):
            _clear(ws._himawari_times)
            _clear(ws._latest_himawari_frame)
            obs, url, data, q = ws._latest_himawari_frame("b13")
        self.assertEqual(obs, "2026-10-30T20:40:00Z")
        self.assertTrue(q["ok"])

    def test_tool_returns_contract_without_network(self):
        def fake_get(url, **kwargs):
            if "sat_img.php" in url:
                return Resp(200, b"", _PAGE)
            if "fd__b13_2050" in url:
                return Resp(200, _jpeg(), "")
            return Resp(404, b"", "")

        with mock.patch.object(requests, "get", fake_get):
            _clear(ws._himawari_times)
            _clear(ws._latest_himawari_frame)
            r = ws.weather_satellite_now(band="infrared")
        sc = r.structuredContent
        self.assertNotIn("error", sc)
        self.assertEqual(sc["band"], "infrared")
        self.assertEqual(sc["band_code"], "b13")
        self.assertTrue(sc["image_verified"]["ok"])
        # 出力は「全円盤＋日本域拡大」の合成パネル。配信画像の大きさは source_size_px
        self.assertEqual(sc["source_size_px"], [601, 601])
        self.assertEqual(sc["size_px"], [1500, 880])
        self.assertTrue(sc["panel"]["ok"], sc["panel"])
        self.assertEqual(sc["observed_at_utc"], "2026-10-30T20:50:00Z")
        self.assertIn("image_path", sc)
        text = "\n".join(getattr(c, "text", "") for c in r.content)
        self.assertIn("実画像の検証", text)
        self.assertTrue(any(getattr(c, "type", "") == "image" for c in r.content))

    def test_network_failure_is_an_error_result(self):
        def boom(url, **kwargs):
            raise requests.ConnectionError("offline")

        with mock.patch.object(requests, "get", boom):
            _clear(ws._himawari_times)
            _clear(ws._latest_himawari_frame)
            r = ws.weather_satellite_now(band="visible")
        self.assertIn("error", r.structuredContent)
        self.assertNotIn("低軌道", r.content[0].text)

    def test_retired_satellite_is_explained(self):
        r = ws.weather_satellite_now(satellite="ひまわり8号")
        self.assertEqual(r.structuredContent["error"], "retired satellite")
        self.assertIn("ひまわり9号", r.content[0].text)


if __name__ == "__main__":
    unittest.main()
