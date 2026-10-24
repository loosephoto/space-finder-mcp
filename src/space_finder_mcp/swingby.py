"""スイングバイ（重力アシスト）航路の実測ルーチン。

太陽系俯瞰図に重ねる「探査機の通過経路」を、**記憶した日付ではなく実測**で作る。

- 経路: JPL Horizons の VECTORS を1回の HTTP で多点取得（太陽中心・黄道 J2000・AU）。
- フライバイ: 経路上の各時刻で惑星位置（Skyfield DE421）との距離を計算し、
  距離の**局所極小**を最接近として拾う。候補は Horizons を細かい刻みで引き直して
  時刻を精密化する（日付を書かない＝ミッション資料の記憶に依存しない）。
- 出力: solar_system.py の ``scene["routes"]`` がそのまま描ける形
  （``points`` = [(x, y, z)] AU、``marks`` = 最接近点の日付・距離・黄経）。

アポロ計画の月スイングバイのように**地球−月系の内側**だけで完結する航路は、
太陽系図では太陽の描画円盤に隠れて読めない。その場合は描かずに理由と代替
（月面地図 ``planetary_orbiter_track(body="moon", orbiter=..., flyby=True)``）を返す。
"""
from __future__ import annotations

import math
import re
from typing import Optional

import requests

from .cache import TTL_DAILY, ttl_cache

UA = {"User-Agent": "space-finder-mcp (MCP; swing-by routes)"}
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
AU_KM = 149597870.7

# Horizons の宇宙機 ID → (経路を引く開始日, 終了日, 表示名)
# 開始日は打ち上げ直後（Horizons の暦開始より前なら自動で繰り下げる）。
SWINGBY_SPANS = {
    "-31": ("1977-09-06", "2026-10-24", "ボイジャー1号"),
    "-32": ("1977-08-21", "2026-10-24", "ボイジャー2号"),
    "-23": ("1972-03-04", "2003-01-23", "パイオニア10号"),
    "-24": ("1973-04-07", "1995-09-30", "パイオニア11号"),
    "-98": ("2006-01-20", "2019-01-01", "ニュー・ホライズンズ"),
    "-77": ("1989-10-20", "1995-12-08", "ガリレオ"),
    "-82": ("1997-10-16", "2004-07-01", "カッシーニ"),
    "-55": ("1990-10-07", "1992-02-08", "ユリシーズ"),
    "-61": ("2011-08-06", "2016-07-05", "ジュノー"),
    "-236": ("2004-08-04", "2011-03-18", "メッセンジャー"),
    "-121": ("2018-10-20", "2026-10-24", "ベピ・コロンボ"),
    # 地球−月系で完結する航路（描けないので理由を返す）
    # 暦の終端を超えると Horizons が「No ephemeris after ...」を返すので、終端より前に切る
    "-399110": ("1969-07-16", "1969-07-27", "アポロ11号 S-IVB"),
    "-399080": ("1968-12-21", "1968-12-26", "アポロ8号 S-IVB"),
}
EARTH_MOON_ONLY = {"-399110", "-399080"}

# 最接近と見なす距離の上限（AU）。天体ごとに変える（惑星は 300万km 級まで、
# 月は 150万km 級まで）。実際の距離は marks に必ず入れる。
_FLYBY_LIMIT = {"月": 0.010, "水星": 0.010, "金星": 0.010, "地球": 0.010,
                "火星": 0.010, "木星": 0.020, "土星": 0.020,
                "天王星": 0.020, "海王星": 0.020, "冥王星": 0.020}

_MON = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def _iso_to_jd(iso: str) -> float:
    """'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM' を JD (UTC 近似) に。"""
    import datetime as dt
    txt = str(iso).strip().replace("T", " ")
    day, _, clock = txt.partition(" ")
    y, m, d = (int(v) for v in day.split("-")[:3])
    hh, mm = 0, 0
    if clock:
        parts = clock.split(":")
        hh, mm = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    base = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    when = dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc)
    return 2440587.5 + (when - base).total_seconds() / 86400.0


def _jd_iso(jd: float) -> str:
    import datetime as dt
    base = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return (base + dt.timedelta(days=float(jd) - 2440587.5)).strftime("%Y-%m-%d %H:%M")


@ttl_cache(TTL_DAILY, maxsize=64)
def _vectors(cmd: str, start: str, stop: str, step: str) -> list:
    """Horizons VECTORS を1回で多点取得。[(jd, x, y, z)]（太陽中心・黄道J2000・AU）。"""
    params = {"format": "text", "COMMAND": "'{}'".format(cmd), "OBJ_DATA": "'NO'",
              "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "'VECTORS'", "CENTER": "'500@10'",
              "REF_PLANE": "'ECLIPTIC'", "START_TIME": "'{}'".format(start),
              "STOP_TIME": "'{}'".format(stop), "STEP_SIZE": "'{}'".format(step),
              "VEC_TABLE": "'1'", "OUT_UNITS": "'AU-D'"}
    r = requests.get(HORIZONS_URL, params=params, headers=UA, timeout=(20, 90))
    r.raise_for_status()
    txt = r.text
    i, j = txt.find("$$SOE"), txt.find("$$EOE")
    if i < 0 or j < 0:
        flat = " ".join(txt.split())
        m = re.search(r"prior to A\.D\.\s*(\d{4})-([A-Z]{3})-(\d{2})\s+(\d{2}):(\d{2})", flat)
        if m:   # 暦の被覆開始より前を指定した → 開始を繰り下げて1回だけ引き直す
            import datetime as dt
            t0 = dt.datetime(int(m.group(1)), _MON[m.group(2).upper()], int(m.group(3)),
                             int(m.group(4)), int(m.group(5))) + dt.timedelta(minutes=5)
            return _vectors(cmd, t0.strftime("%Y-%m-%d %H:%M"), stop, step)
        raise ValueError("Horizons に座標ブロックがありません: %s" % flat[:180])
    out, cur = [], None
    for line in txt[i + 5:j].splitlines():
        s = line.strip()
        if "= A.D." in s:
            head = s.split("= A.D.")
            cur = {"jd": float(head[0].strip()), "iso": head[1].strip()[:19]}
        elif s.startswith("X =") and cur is not None:
            n = re.findall(r"[-+0-9.Ee]+", s)
            out.append((cur["jd"], float(n[0]), float(n[1]), float(n[2])))
    return out


def _dist_au(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _planet_pos(eph, sun, ts, key, jd):
    """惑星の日心位置（AU・黄道J2000 の X,Y,Z）。

    Horizons の VECTORS は REF_PLANE=ECLIPTIC なので、Skyfield の position.au
    （ICRS/赤道座標）をそのまま引き算すると**回転の分だけ距離が狂う**
    （実測: 打ち上げ直後の地球との距離が 0.115 AU＝1700万km に見えた）。
    必ず黄道フレームに回してから比較する。
    """
    # Horizons の REF_PLANE='ECLIPTIC' は **J2000 の黄道**。Skyfield の ecliptic_frame は
    # 「その時点の黄道（of date）」なので、21年で約0.3°（木星で約400万km）ずれる。
    # 必ず ecliptic_J2000_frame を使う（実測: これを of date にすると木星距離が 0.027 AU 狂った）。
    from skyfield.framelib import ecliptic_J2000_frame
    v = eph[key].at(ts.tt_jd(jd)) - sun.at(ts.tt_jd(jd))
    q = v.frame_xyz(ecliptic_J2000_frame).au
    return (q[0], q[1], q[2])


def _find_encounters(pts, planets, eph, sun, ts, cmd=None, min_gap_days=20.0):
    """経路点列と惑星位置の距離の局所極小を候補にし、Horizons で精密化して確定する。

    粗い刻み（例 30 日）では最接近そのものを飛ばす。実測: ボイジャー1号の木星最接近は
    30 日刻みでは 1.57 AU にしか見えない（実際は約 35 万 km）。そこで**距離が極小になる
    候補を全部拾い**、候補の前後を 1 時間刻みで引き直して最接近時刻・距離を測り直す。
    記憶した日付に頼らないので、どのミッションでも同じ手順で確定できる。
    """
    hits = []
    if len(pts) < 3:
        return hits
    times = [p[0] for p in pts]
    step_days = abs(times[-1] - times[0]) / max(1, len(times) - 1)
    for jname, key in planets:
        dists = [_dist_au((x, y, z), _planet_pos(eph, sun, ts, key, jd))
                 for (jd, x, y, z) in pts]
        for i in range(1, len(pts) - 1):
            d0, d1, d2 = dists[i - 1], dists[i], dists[i + 1]
            # 距離の極小＝フライバイ候補。ただし候補を絞る: 粗い刻みで 0.15 AU を超える
            # 極小は「すれ違い」ではない（地球距離の年周期などで候補が数十個出て、
            # 1候補=1回の Horizons 呼び出しでタイムアウトする）。近い順に最大8件だけ精密化。
            if d1 <= d0 and d1 <= d2 and d1 < 0.15:
                hits.append({"body": jname, "key": key, "jd": times[i], "au": d1,
                             "xyz": (pts[i][1], pts[i][2], pts[i][3]), "fine": []})
    hits.sort(key=lambda h: h["jd"])
    hits.sort(key=lambda h: h["au"])
    hits = hits[:8]          # Horizons 呼び出しの上限（近い候補から精密化する）
    hits.sort(key=lambda h: h["jd"])
    merged = []
    for h in hits:   # 同じフライバイを二重に拾わない
        if merged and h["jd"] - merged[-1]["jd"] < min_gap_days:
            if h["au"] < merged[-1]["au"]:
                merged[-1] = h
            continue
        merged.append(h)
    out = []
    for h in merged:
        lim = _FLYBY_LIMIT.get(h["body"], 0.02)
        best = h
        if cmd:
            # 候補の前後を細かく引き直す（粗い刻みの間隔に合わせ、1〜15日）
            wd = min(max(step_days * 2.0, 1.0), 15.0)
            try:
                fine = _vectors(cmd, _jd_iso(h["jd"] - wd), _jd_iso(h["jd"] + wd), "1 h")
                for (jd, x, y, z) in fine:
                    d = _dist_au((x, y, z), _planet_pos(eph, sun, ts, h["key"], jd))
                    if d < best["au"]:
                        best = {"body": h["body"], "key": h["key"], "jd": jd, "au": d,
                                "xyz": (x, y, z), "fine": fine}
                best.setdefault("fine", fine)
            except Exception:
                pass
        if best["au"] < lim:
            out.append(best)
    return out


def probe_route(cmd: str, name: str, color, planets, eph, sun, ts,
                when_jd: Optional[float] = None,
                span: Optional[tuple] = None, step: str = "2 d") -> dict:
    """探査機の通過経路＋スイングバイ点を、俯瞰図が描ける形で返す（認証不要）。

    cmd は Horizons の負ID（例 "-31"）。span 省略時は SWINGBY_SPANS の表を使い、
    表に無い機体は「指定時刻までの過去1年」を描く。
    """
    span = span or SWINGBY_SPANS.get(str(cmd))
    trail_only = False
    if span:
        start, stop = span[0], span[1]
    else:
        end = float(when_jd or _iso_to_jd("2026-10-24"))
        start, stop = _jd_iso(end - 365.0), _jd_iso(end)
        trail_only = True
    pts = _vectors(cmd, start, stop, step)
    if not pts:
        raise ValueError("Horizons が経路点を返しませんでした（{}〜{}）".format(start, stop))
    rs = [math.sqrt(x * x + y * y + z * z) for (_, x, y, z) in pts]
    if max(rs) < 0.012:
        # 地球−月系の内側だけで完結する航路（アポロの月スイングバイ等）。
        # 太陽系図では太陽の描画円盤に隠れて読めないので、描かずに理由を返す。
        return {"kind": "spacecraft", "name": name, "error": "地球−月系の内側（最大 {:.4f} AU）だけを飛ぶ航路のため、"
                                       "太陽系図では太陽の描画円盤に隠れて描けません。"
                                       "月面地図（planetary_orbiter_track(body=\"moon\", "
                                       "orbiter=\"apollo\", flyby=True)）を使ってください。".format(max(rs)),
                "earth_moon_only": True, "max_au": max(rs)}

    enc = [] if trail_only else _find_encounters(pts, planets, eph, sun, ts, cmd=cmd)
    all_pts = {round(p[0], 6): (p[0], p[1], p[2], p[3]) for p in pts}
    marks = []
    for hit in enc:
        # 精密化で得た細かい点列を経路に足す（折れ線が最接近点を切り落とさない）
        for p in (hit.get("fine") or []):
            all_pts[round(p[0], 6)] = p
        jd, x, y, z = hit["jd"], hit["xyz"][0], hit["xyz"][1], hit["xyz"][2]
        rr = math.sqrt(x * x + y * y + z * z)
        marks.append({"id": "{}@{}".format(hit["body"], _jd_iso(jd)[:10]),
                      "label": "{} {:,.0f}km".format(hit["body"], hit["au"] * AU_KM).replace(",", ""),
                      "date": _jd_iso(jd)[:10],
                      "body": hit["body"],
                      "r_au": round(rr, 6), "proj_au": round(math.hypot(x, y), 6),
                      "dist_km": round(hit["au"] * AU_KM),
                      "lon_deg": round(math.degrees(math.atan2(y, x)) % 360.0, 3),
                      "jd": round(jd, 6)})

    ordered = [all_pts[k] for k in sorted(all_pts)]
    return {"kind": "spacecraft", "name": name, "color": list(color),
            "points": [(p[1], p[2], p[3]) for p in ordered],
            "marks": marks, "closed": False, "trail_only": trail_only,
            "r_max_au": round(max(rs), 4), "out_of_frame": bool(max(rs) > 45.0),
            "span": [start, stop], "source": "JPL Horizons 状態ベクトル（実測）"}
