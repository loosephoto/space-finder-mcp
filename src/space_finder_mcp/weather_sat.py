"""世界の気象観測衛星のリアルタイム実画像を返す（認証不要）。

Phase 0: 日本の静止気象衛星 ひまわり9号 のフルディスク実画像（NICT/Kochi ミラーの
静的PNGタイル）。プロバイダ抽象層を設け、将来 GOES（AWS Open Data）や Meteosat 等を
同インターフェースで追加できる構造にしている（汎用化の土台）。

設計の要点（Phase 0〜1の共通IF）:
- 各衛星 = プロバイダ: 衛星名解決 → 帯域コード → 最新フレーム取得 → PNG。
- 「最新フレーム」は最新エイリアスが無いため、直近の10分刻みUTCスロットを
  プローブして一番新しい 200 を選ぶ（Himawari は10分観測周期）。
- 鮮度（観測時刻・取得時刻・遅延分）を structuredContent に必ず載せ、
  ホストLLMが「リアルタイム」と「数分前」を混同しないようにする。

出典: 気象庁 ひまわり9号 の観測画像 ／ 配信 = himawari.asia（NICT/Kochi ミラー、認証不要）
"""
from __future__ import annotations

import base64
import datetime
import io

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import ttl_cache
from .img_common import encode_jpeg, media_link_line, save_output
from .input_utils import as_int

_HIMAWARI_BASE = "https://himawari.asia/img/D531106/{band}/{size}/{ts}_0_0.png"
_UA = {"User-Agent": "space-finder-mcp/0.29 (MCP; weather satellite imagery)"}

_HIMAWARI_BANDS = {
    "visible":    {"code": "1d", "name": "可視光", "note": "昼間の雲・台風の形状"},
    "infrared":   {"code": "3d", "name": "赤外線（色付け）", "note": "昼夜問わず雲頂高度・温度"},
    "true_color": {"code": "8d", "name": "真色", "note": "自然な見た目に近い合成"},
}


def _resolve_himawari_name(satellite):
    s = str(satellite or "").strip().lower()
    s = s.replace("ひまわり", "himawari").replace("号", "").replace(" ", "").replace("-", "")
    if s in ("", "himawari", "himawari9", "himawari09"):
        return "himawari9"
    if s in ("himawari8", "himawari08"):
        return "himawari8"
    return ""


@ttl_cache(seconds=30)  # 鮮度優先: 30秒間だけ同一スロットを再利用（配信遅延への追随を早める）
def _latest_himawari_frame(band_code, size):
    now = datetime.datetime.now(datetime.timezone.utc)
    for back in range(0, 9):
        t = now - datetime.timedelta(minutes=back * 10)
        t = t.replace(minute=(t.minute // 10) * 10, second=0, microsecond=0)
        ts = t.strftime("%Y/%m/%d/%H%M%S")
        url = _HIMAWARI_BASE.format(band=band_code, size=size, ts=ts)
        try:
            r = requests.get(url, headers=_UA, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content:
            return (t.strftime("%Y-%m-%dT%H:%M:%SZ"), url, r.content)
    raise ValueError("最新のひまわり画像が見つかりませんでした（配信遅延の可能性）。数分後に再試行してください。")


def weather_satellite_now(satellite="himawari9", band="visible", size=550):
    """世界の気象観測衛星のリアルタイム実画像を返す（認証不要）。

    例:「ひまわり9号の最新画像」「気象衛星の今の雲画像」「ひまわりの赤外線画像」
    現在は日本の静止気象衛星 ひまわり9号 のフルディスク実画像に対応。
    NICT/Kochi ミラーの10分刻みタイルから最新フレームを探し、画像を返す。

    戻り: structuredContent に JSON（衛星・帯域・鮮度・画像URL）、content に
    表示用テキスト＋インライン画像。鮮度（観測時刻/取得時刻/遅延分）は必ず含める。

    Args:
        satellite: 衛星名。既定 himawari9。和名も可（ひまわり / ひまわり9号）。
            退役のひまわり8号は「表示できない」と正直に返す。
        band: 帯域。visible / infrared / true_color。
        size: 出力サイズpx（既定 550=フルディスク単一PNG）。
    """
    size = as_int(size, 550, 200, 550)

    b = (band or "visible").strip().lower()
    if b not in _HIMAWARI_BANDS:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "帯域 '{}' は対応していません。指定可能: ".format(band)
                + ", ".join("{}（{}）".format(k, _HIMAWARI_BANDS[k]["name"]) for k in _HIMAWARI_BANDS)
                + '。例: band="visible"'))],
            structuredContent={"error": "invalid band", "band": band,
                               "available_bands": sorted(_HIMAWARI_BANDS.keys())},
        )
    band_spec = _HIMAWARI_BANDS[b]

    canon = _resolve_himawari_name(satellite)
    if canon == "himawari8":
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "ひまわり8号は 2024年12月に運用を終了し、現在はひまわり9号が"
                '本運用中です。最新のリアルタイム画像をご希望なら satellite="himawari9"'
                "（または ひまわり9号）を指定してください。"))],
            structuredContent={"error": "retired satellite", "satellite": satellite,
                               "note": "Himawari-8 retired 2024-12; Himawari-9 is operational",
                               "active": "himawari9"},
        )
    if not canon:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "衛星 '{}' を特定できませんでした。現役のリアルタイム画像は"
                "ひまわり9号（himawari9 / ひまわり）のみ対応しています。".format(satellite)))],
            structuredContent={"error": "unknown satellite", "satellite": satellite,
                               "available": ["himawari9"]},
        )

    try:
        observed_iso, url, png = _latest_himawari_frame(band_spec["code"], size)
    except ValueError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=str(e))],
            structuredContent={"error": "frame not found", "source": "himawari.asia"},
        )
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text="ひまわり画像の取得に失敗しました: {}".format(e))],
            structuredContent={"error": str(e), "source": "himawari.asia"},
        )
    retrieved_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png)).convert("RGB")
        jpeg = encode_jpeg(img)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像の変換に失敗しました: {}".format(e))],
            structuredContent={"error": str(e), "source": "himawari.asia"},
        )
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg",
                        altText="ひまわり9号 {} 最新画像".format(band_spec["name"]))
    out_path = save_output(jpeg, "weather_satellite_now", "jpg")

    try:
        obs = datetime.datetime.fromisoformat(observed_iso.replace("Z", "+00:00"))
        ret = datetime.datetime.fromisoformat(retrieved_iso.replace("Z", "+00:00"))
        latency_min = round((ret - obs).total_seconds() / 60.0, 1)
    except Exception:
        latency_min = None
    obs_disp = observed_iso.replace("T", " ").replace("Z", "") + " UTC"

    latency_line = ("⏱ 配信遅延: 約 {} 分".format(latency_min)
                    if latency_min is not None else "⏱ 配信遅延: 測定不能")
    text_lines = [
        media_link_line("生成した画像を開く（ひまわり9号 最新画像）", path=out_path, kind="image"),
        "🛰 **ひまわり9号（HIMAWARI-9）** の最新実画像（{}）:".format(band_spec["name"]),
        "🕐 観測時刻: {}（UTC）".format(obs_disp),
        latency_line,
        "🎛 帯域: {}（{}）・フルディスク（{}px）".format(band_spec["name"], band_spec["note"], size),
        "🛰 位置: 静止軌道 東経140.7度・赤道上（約35,800 km）",
        "",
        "### ⚠️ 鮮度の注記",
        "- 観測時刻は上記のとおり。ひまわり9号は10分ごとにフルディスクを撮影し、配信に数分かかります",
        "- 静止衛星のため日本付近（東経140.7度）を常時カバー。南北アメリカ・欧州等は他の衛星（GOES/Meteosat）の対象",
        "- 画像URL: {}（このURLは観測時刻固定のため、時間が経つと無効になります。最新を再取得するには再実行）".format(url),
        "出典: 気象庁 ひまわり9号（JMA）／ 配信 himawari.asia（NICT/Kochi ミラー、認証不要）",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(text_lines)), imgc],
        structuredContent={
            "satellite": "HIMAWARI-9", "agency": "JMA/JAXA", "country": "日本",
            "band": b, "band_name": band_spec["name"], "band_code": band_spec["code"],
            "region": "full-disk (geostationary 140.7E)",
            "observed_at_utc": observed_iso, "retrieved_at_utc": retrieved_iso,
            "latency_min": latency_min, "size_px": size,
            "image_url": url, "image_path": out_path,
            "source": "JMA Himawari-9 via himawari.asia (NICT/Kochi mirror, no auth)",
        },
    )

# GEO/LEO拡張プロバイダ。URLは実取得検証済み。
_EXT_UNAVAILABLE={"meteor-mn2-3":("unavailable","Roscosmosの固定公開画像URLなし"),"meteor-mn2-4":("unavailable","Roscosmosの固定公開画像URLなし"),"meteor-m-n2-3":("unavailable","Roscosmosの固定公開画像URLなし"),"meteor-m-n2-4":("unavailable","Roscosmosの固定公開画像URLなし"),"dmsp-f17":("restricted","DoD運用・公開画像なし"),"dmsp-f18":("restricted","DoD運用・公開画像なし"),"wsf-m1":("restricted","DoD運用・公開画像なし"),"cosmic-2":("non_image_product","GNSS-ROの数値プロファイルで画像なし"),"triton":("non_image_product","GNSS-ROの数値プロファイルで画像なし")}
_EXT_ALIAS={"ひまわり":"himawari9","ひまわり9号":"himawari9","gk2a":"gk2a","千里眼2a":"gk2a","しずく":"gcom-w","gcom-w1":"gcom-w"}
def _ext_canon(v):
 s=" ".join(str(v or "").lower().strip().split()).replace("号","").replace("_","-").replace(" ","");return {"goes-19":"goes19","goes-18":"goes18","noaa-20":"noaa20","noaa-21":"noaa21","snpp":"snpp","metop-b":"metopb","metop-c":"metopc","fy-3d":"fy3d","fy-3f":"fy3f","fy-4b":"fy4b","earth-care":"earthcare"}.get(s,_EXT_ALIAS.get(s,s))
def _ext_get(u,timeout=60):
 r=requests.get(u,headers=_UA,timeout=timeout);return (u,r.content) if r.status_code==200 and "image" in r.headers.get("Content-Type","") and r.content else None
def _ext_gibs(layer,size=1000):
 now=datetime.datetime.now(datetime.timezone.utc)
 for back in range(4):
  day=(now-datetime.timedelta(days=back)).strftime("%Y-%m-%d");u="https://wvs.earthdata.nasa.gov/api/v1/snapshot?REQUEST=GetSnapshot&LAYERS="+layer+"&CRS=EPSG:4326&TIME="+day+"&WIDTH="+str(size)+"&HEIGHT="+str(size)+"&FORMAT=image/jpeg&BBOX=-180,-90,180,90";x=_ext_get(u,90)
  if x and len(x[1])>10000:return x[0],x[1],day
 raise ValueError("NASA GIBS画像なし")
def _ext_eum(layer,lon,size=1200):
 u="https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap&layers="+layer+",backgrounds:ne_10m_coastline&bbox=-6500000,-6500000,6500000,6500000&width="+str(size)+"&height="+str(size)+"&srs=AUTO:97004,9001,"+str(lon)+",0&styles=&format=image/jpeg&bgcolor=0xCCCCCC";x=_ext_get(u,90)
 if not x:raise ValueError("EUMETSAT画像なし")
 return x[0],x[1],None
def _ext_fetch(s,size):
 if s in {"goes19","goes18"}:
  sat="GOES19" if s=="goes19" else "GOES18";u="https://cdn.star.nesdis.noaa.gov/"+sat+"/ABI/FD/GEOCOLOR/1808x1808.jpg";x=_ext_get(u,60)
  if not x:raise ValueError("NOAA STAR画像なし")
  return x[0],x[1],None,"GeoColor","NOAA/NESDIS STAR"
 if s in {"noaa20","noaa21","snpp"}:
  layer={"noaa20":"VIIRS_NOAA20_CorrectedReflectance_TrueColor","noaa21":"VIIRS_NOAA21_CorrectedReflectance_TrueColor","snpp":"VIIRS_SNPP_CorrectedReflectance_TrueColor"}[s];u,b,d=_ext_gibs(layer,min(size,1000));return u,b,d,"VIIRS true color","NASA GIBS"
 if s in {"metopb","metopc"}:
  u,b,d=_ext_eum("eps:m02_rgb_124" if s=="metopb" else "eps:m03_rgb_124",0,min(size,1400));return u,b,d,"RGB 124","EUMETSAT EPS WMS"
 paths={"fy3d":"FY3D/MIPS/FY3D_MERSI_GLOBAL.jpg","fy3f":"FY3F/THUMBNAIL/FY3F_MERSI_TPW_ASC.jpg","fy4b":"FY4B/AGRI/THUMBNAIL/FY4B_AGRI_DISK_GCLR.jpg"}
 if s in paths:
  x=_ext_get("https://img.nsmc.org.cn/CLOUDIMAGE/"+paths[s],60)
  if not x:raise ValueError("NSMC画像なし")
  return x[0],x[1],None,s,"CMA/NSMC"
 if s=="earthcare":
  now=datetime.datetime.now(datetime.timezone.utc);day=now.strftime("%Y/%m/%d");ds=now.strftime("%Y%m%d")
  for orbit in ("13058B","13057B","13059B","13056B"):
   u="https://www.eorc.jaxa.jp/EARTHCARE/Quicklook/data/CPR_ECO/integrated_radar_reflectivity_10km/"+day+"/CPR_ECO_integrated_radar_reflectivity_10km_"+ds+"_"+orbit+".jpg";x=_ext_get(u,60)
   if x:return x[0],x[1],None,"CPR reflectivity swath","JAXA/EORC QuickLook"
  raise ValueError("EarthCARE QuickLookなし")
 raise ValueError("拡張プロバイダ未実装")
def weather_satellite_now_extended(satellite="himawari9",band="visible",size=550):
 size=as_int(size,550,200,1808);s=_ext_canon(satellite)
 if s in _EXT_UNAVAILABLE:
  code,msg=_EXT_UNAVAILABLE[s];return CallToolResult(content=[TextContent(type="text",text=msg)],structuredContent={"error":code,"satellite":satellite,"message":msg})
 if s=="himawari9":return weather_satellite_now(satellite=satellite,band=band,size=size)
 try:u,b,o,product,source=_ext_fetch(s,size);from PIL import Image;im=Image.open(io.BytesIO(b)).convert("RGB");jpg=encode_jpeg(im)
 except (ValueError,requests.RequestException) as e:return CallToolResult(content=[TextContent(type="text",text=str(e))],structuredContent={"error":"fetch_failed","satellite":satellite,"message":str(e)})
 except Exception as e:return CallToolResult(content=[TextContent(type="text",text=str(e))],structuredContent={"error":"image_decode_failed","satellite":satellite})
 retrieved=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z");path=save_output(jpg,"weather_satellite_now","jpg");text="\n".join([media_link_line("生成した画像を開く",url=u,kind="image"),"🛰 **"+str(satellite)+"**（"+product+"）","観測: "+str(o or "配信元非公開"),"取得: "+retrieved,"出典: "+source])
 payload={"satellite":satellite,"canonical":s,"product":product,"source":source,"observed_at_utc":o,"retrieved_at_utc":retrieved,"image_url":u,"image_path":path,"size_px":[im.width,im.height],"image_semantics":"image_product"}
 return CallToolResult(content=[TextContent(type="text",text=text),ImageContent(type="image",data=base64.b64encode(jpg).decode("ascii"),mimeType="image/jpeg",altText=str(satellite)+" 気象衛星画像")],structuredContent=payload)
