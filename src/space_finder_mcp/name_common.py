"""天体名・衛星名の解決（表記ゆれ・言語差・名前→座標）を1か所に集約する。

同じ「自然な入力が 0 件になる」問題が各天文台ツールで別々に発生していたため、
**共通のフォールバック段階**としてまとめる（AGENTS 規約5: 共通処理は集約）。

段階:
  1. 内蔵テーブル（ツール側。速い・オフライン）
  2. `name_variants()` / `like_patterns()` — 同一言語内の表記ゆれ
     （空白 / アンダースコア / 連結 / 大小。アーカイブは観測者の入力そのままを持つ）
  3. `expand_terms()` — 言語差（和名 → 英語名）
  4. `resolve_object()` — 名前 → 座標（Sesame/CDS 経由で SIMBAD/NED を横断。認証不要）
  5. それでも駄目なら **候補を提示して停止**（推測しない。AGENTS 規約8）

実測の根拠:
  - ALMA の target_name は "HL Tau" / "HL_Tau" / "HLTau" / "HL_tau" が併存（完全一致で 0 件）
  - CADC は内蔵テーブルに無い名前（M104, Sombrero, HL Tau, 和名）で即エラー
  - OSCAR / CelesTrak は英語キーのみ（"ひまわり", "ひのだ" 等の和名で 0 件）
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from .cache import TTL_DAILY, ttl_cache

UA = {"User-Agent": "space-finder-mcp/0.27 (MCP; name resolver)"}
SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/S?{}"

_NAME_SPLIT = re.compile(r"[\s_]+")

# 和名 → 英語名/略称（衛星・宇宙機・一部の天文台）。検索語の展開に使う。
JA_ALIASES: dict = {
    # ---- 日本の気象・地球観測衛星（番号付きを先に置く: 展開順で具体名が先に来る） ----
    "ひまわり9号": ["himawari-9"], "ひまわり8号": ["himawari-8"],
    "ひまわり7号": ["himawari-7"], "ひまわり6号": ["himawari-6"],
    "だいち4号": ["alos-4"], "だいち3号": ["alos-3"], "だいち2号": ["alos-2"],
    "いぶき2号": ["gosat-2"], "いぶき": ["gosat"],
    "ひまわり": ["himawari"], "だいち": ["alos"],
    "しずく": ["gcom-w", "gcom-w1"], "しきさい": ["gcom-c", "gcom-c1"],
    "みどり2": ["adeos-2", "midori-2"], "みどり": ["adeos", "midori"],
    "ふよう1号": ["jers-1", "fuyo-1"], "もも1号": ["mos-1", "momo-1"],
    "あじさい": ["ajisai", "egs"], "きらめき": ["ets-viii"],
    "準天頂衛星": ["qzss"], "みちびき": ["qzss", "michibiki"],
    # ---- 日本の科学衛星・探査機 ----
    "ひので": ["hinode", "solar-b", "hinode (solar-b)"], "ようこう": ["yohkoh", "solar-a"],
    "すざく": ["suzaku", "astro-e2"], "あすか": ["asca", "astro-d"],
    "ぎんが": ["ginga", "astro-c"], "てんま": ["tenma", "astro-b"],
    "はくちょう": ["hakucho", "corsa-b"], "ひてん": ["hiten", "muses-a"],
    "あらせ": ["arase", "erg"], "れいめい": ["reimei", "index"],
    "ひさき": ["hisaki", "sprint-a"],
    "かぐや": ["kaguya", "kaguya (selene)"], "あかつき": ["akatsuki", "planet-c (akatsuki)"],
    "はやぶさ2": ["hayabusa2", "hayabusa 2"], "はやぶさ": ["hayabusa"],
    "のぞみ": ["nozomi", "planet-b"], "すいせい": ["suisei", "planet-a"],
    "さきがけ": ["sakigake", "ms-t5"], "おおすみ": ["ohsumi"],
    # ---- 日本の有人・輸送 ----
    "国際宇宙ステーション": ["iss"], "宇宙ステーション": ["iss"],
    "きぼう": ["kibo", "jem"], "こうのとり": ["htv"],
    # ---- 宇宙望遠鏡（衛星なので CelesTrak/OSCAR でも使う） ----
    "ハッブル宇宙望遠鏡": ["hubble"], "ハッブル": ["hubble"],
    "ジェイムズ・ウェッブ": ["jwst", "james webb space telescope"],
    "ジェームズ・ウェッブ": ["jwst", "james webb space telescope"],
    "スピッツァー": ["spitzer"], "チャンドラ": ["chandra"],
    "ハーシェル": ["herschel"], "ケプラー": ["kepler"], "ガイア": ["gaia"],
    "ヒッパルコス": ["hipparcos"],
    # ---- 中国・その他（日本語で言及される機体） ----
    "天宮": ["tiangong"], "天和": ["tianhe"], "問天": ["wentian"], "夢天": ["mengtian"],
    "神舟": ["shenzhou"], "嫦娥": ["chang'e", "change-4"], "天問": ["tianwen"],
    # ---- 深宇宙天体: 銀河 ----
    "アンドロメダ銀河": ["Andromeda Galaxy", "M31"], "アンドロメダ": ["Andromeda Galaxy", "M31"],
    "ソンブレロ銀河": ["Sombrero Galaxy", "M104"], "ソンブレロ": ["Sombrero Galaxy"],
    "子持ち銀河": ["Whirlpool Galaxy", "M51"], "回転花火銀河": ["Pinwheel Galaxy", "M101"],
    "三角座銀河": ["Triangulum Galaxy", "M33"], "黒眼銀河": ["Black Eye Galaxy", "M64"],
    "大マゼラン雲": ["Large Magellanic Cloud", "LMC"],
    "小マゼラン雲": ["Small Magellanic Cloud", "SMC"],
    "いて座Aスター": ["Sagittarius A*", "Sgr A*"], "いて座A*": ["Sagittarius A*", "Sgr A*"],
    # ---- 深宇宙天体: 星雲・星団 ----
    "オリオン大星雲": ["Orion Nebula", "M42"], "オリオン星雲": ["Orion Nebula", "M42"],
    "かに星雲": ["Crab Nebula", "M1"], "プレアデス星団": ["Pleiades", "M45"],
    "プレアデス": ["Pleiades", "M45"], "バラ星雲": ["Rosette Nebula"],
    "わし星雲": ["Eagle Nebula", "M16"], "三裂星雲": ["Trifid Nebula", "M20"],
    "北アメリカ星雲": ["North America Nebula", "NGC 7000"],
    "馬頭星雲": ["Horsehead Nebula", "B33"], "カリーナ星雲": ["Carina Nebula"],
    "タランチュラ星雲": ["Tarantula Nebula"], "リング星雲": ["Ring Nebula", "M57"],
    "ばら星雲": ["Rosette Nebula"],
    # ---- 明るい恒星（アーカイブ検索・座標解決で使う） ----
    "シリウス": ["Sirius"], "カノープス": ["Canopus"], "アルクトゥルス": ["Arcturus"],
    "ベガ": ["Vega"], "カペラ": ["Capella"], "リゲル": ["Rigel"], "プロキオン": ["Procyon"],
    "ベテルギウス": ["Betelgeuse"], "アルタイル": ["Altair"], "アルデバラン": ["Aldebaran"],
    "アンタレス": ["Antares"], "スピカ": ["Spica"], "ポルックス": ["Pollux"],
    "フォーマルハウト": ["Fomalhaut"], "デネブ": ["Deneb"], "レグルス": ["Regulus"],
}


def first_token(obj: str) -> str:
    """天体名の第1語（該当なしのときに候補を探す LIKE の起点に使う）。"""
    toks = [t for t in _NAME_SPLIT.split(" ".join(str(obj).split())) if t]
    return toks[0] if toks else ""


def name_variants(obj: str) -> list:
    """表記ゆれ候補（空白 / アンダースコア / 連結形）を返す。"""
    o = " ".join(str(obj).split())
    toks = [t for t in _NAME_SPLIT.split(o) if t]
    cand = {o, o.replace(" ", "_"), o.replace("_", " "),
            o.replace(" ", ""), o.replace("_", "")}
    if toks:
        cand |= {" ".join(toks), "_".join(toks), "".join(toks)}
    return sorted(v for v in cand if v)


def like_patterns(obj: str, max_pats: int = 12) -> list:
    """語間をワイルドカードにした LIKE パターン（大小・区切りの揺れを吸収）。

    ALMA/CADC の TAP には UPPER()/REPLACE() が無く（ALMA は HTTP 400 を実測）、
    LIKE も大文字小文字を区別するため、語ごとの大小の組み合わせを有限個投げる。
    単語1つなら前方一致も許す（"Orion" → "Orion KL"）。
    """
    import itertools
    toks = [t for t in _NAME_SPLIT.split(" ".join(str(obj).split())) if t]
    if not toks:
        return []

    def _case_variants(t):
        out, seen = [], set()
        for x in (t, t.upper(), t.capitalize(), t.lower()):
            if x not in seen:
                seen.add(x); out.append(x)
        return out

    combos = list(itertools.product(*[_case_variants(t) for t in toks]))
    combos.sort(key=lambda c: sum(1 for a, b in zip(c, toks) if a != b))
    pats = ["%".join(c) for c in combos[:max_pats]]
    if len(toks) == 1:
        pats.append(toks[0] + "%")
    return pats


def expand_terms(obj: str) -> list:
    """検索語を「入力そのもの + 和名なら英語名」に展開する（順序は入力優先）。"""
    o = " ".join(str(obj).split())
    terms = [o] if o else []
    for key, ens in JA_ALIASES.items():
        if key and (key in o or o.lower() == key.lower()):
            terms.extend(ens)
    # 重複除去（順序維持）
    seen, out = set(), []
    for t in terms:
        tl = t.lower()
        if tl and tl not in seen:
            seen.add(tl); out.append(t)
    return out


@ttl_cache(TTL_DAILY, maxsize=512, skip_if=lambda r: not r)
def _sesame_lookup(name: str, timeout: int = 30) -> Optional[dict]:
    """Sesame/CDS に1回問い合わせて座標を返す（1つの名前に対して）。"""
    o = " ".join(str(name).split())
    if not o:
        return None
    try:
        r = requests.get(SESAME.format(requests.utils.quote(o)), headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return None

    def _tag(e):
        return e.tag.split("}")[-1]

    def _txt(node, name):
        for e in node.iter():
            if _tag(e) == name and e.text:
                return e.text.strip()
        return None

    target = next((e for e in root.iter() if _tag(e) == "Target"), None)
    if target is None:
        return None
    positions = []
    for res in (e for e in target.iter() if _tag(e) == "Resolver"):
        ra, dec = _txt(res, "jradeg"), _txt(res, "jdedeg")
        if ra is None or dec is None:
            continue
        try:
            positions.append({"ra_deg": float(ra), "dec_deg": float(dec),
                              "matched_name": _txt(res, "oname") or o,
                              "otype": _txt(res, "otype"),
                              "resolver": (res.get("name") or "").replace("Sc=", "").split("(")[0].strip()})
        except ValueError:
            continue
    if not positions:
        return None
    best = positions[0]
    spread = 0.0
    if len(positions) > 1:
        spread = max(abs(p["ra_deg"] - best["ra_deg"]) + abs(p["dec_deg"] - best["dec_deg"])
                     for p in positions[1:])
    return {"input_name": o, "ra_deg": best["ra_deg"], "dec_deg": best["dec_deg"],
            "matched_name": best["matched_name"], "otype": best["otype"],
            "resolver": best["resolver"] or "Sesame",
            "all_positions": positions, "resolver_spread_deg": round(spread, 4)}


@ttl_cache(TTL_DAILY, maxsize=512, skip_if=lambda r: not r)
def resolve_object(obj: str, timeout: int = 30) -> Optional[dict]:
    """天体名 → 座標（ICRS, 度）。Sesame/CDS 経由で SIMBAD/NED/VizieR を横断（認証不要）。

    1) 入力そのままで解決 → 2) 和名なら英語名（JA_ALIASES）で再試行、の順。
    愛称（Sombrero）・Messier 番号・星表名の大半を解決できる。解決できない場合は None。
    複数リゾルバの位置が食い違う場合は全位置を返し、呼び出し側が判断できるようにする。
    """
    o = " ".join(str(obj).split())
    if not o:
        return None
    terms = expand_terms(o)
    for i, term in enumerate(terms[:4]):
        hit = _sesame_lookup(term, timeout)
        if hit:
            hit = dict(hit)
            hit["input_name"] = o
            if i > 0:
                hit["matched_via_alias"] = term
                hit["resolver"] = "{}（和名→英語名 {}）".format(hit.get("resolver") or "Sesame", term)
            return hit
    return None
