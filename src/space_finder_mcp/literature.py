"""学術文献（惑星科学・宇宙の一次情報）を横断検索し、記述の根拠（DOI付き）を返す。

なぜ必要か: 本サーバーの惑星・天体の説明は Wikipedia / Wikidata と LLM の既存知識に依存しており、
「その記述の根拠となる一次文献」を返せなかった。本モジュールは公開の書誌 API を横断し、DOI 付きの
文献（タイトル・著者・年・誌名・被引用数・要旨）を**根拠**として返す。

データ経路（実測 2026-09。すべて認証不要・JSON/Atom/XML）:
- **OpenAlex**（第一候補・CC0）: 要旨（abstract_inverted_index を復元）・被引用数・OAリンクを返す。
  **`search=` は英語クエリでだけ信頼できる**: 日本語を投げると全文検索として扱われ、まったく無関係な
  文献が返る（実測: 「月 永久影 水氷」→ IPBES 生物多様性評価レポート、「火星 大気 脱出 メカニズム」
  → 草津白根火山の論文）。よって日本語入力は `to_english()` で英語語へ置換してから投げる。
- **Crossref**: DOI・出版社・誌名・種類。`query.bibliographic` は緩い部分一致なので、日本語では
  無関係な文献が混ざる（実測: 「月 永久影 水氷」→ 中国語の水利工学論文）。また **title が空の
  レコードが混ざる**ため除外する。
- **NASA NTRS**: 技術報告（ミッション設計・機器・探査計画）＋PDF リンク。
- **JAXAリポジトリ**（WEKO3 `/api/records`）: JAXA/ISAS の研究報告。**日本語クエリが効く**。
- **J-STAGE**（`service=3&keyword=`）: 国内学会誌（日本惑星科学会「遊・星・人」等）。
  **`material` / `article_title` は ERR_001 / ERR_012 を返すので `keyword` を使う**（実測）。
  また **`ERR_001` は「該当なし」**（"大気流出"、"zzzqqqxyz" が ERR_001 / "火星" は status=0 で 159件）で
  あってエラーではない。`WARN_002` は結果を伴う警告（"大気" で 2,705件）。利用規約により
  「表示情報提供元: J-STAGE」＋リンクの表示が必要（credit に含めてある）。
  応答は Content-Type に charset が無く `r.text` が文字化けするため bytes を UTF-8 で解釈する。
- **CiNii Research**: 国内論文・紀要（`format=json`）。
- **Zenodo / DataCite**: データセット・ソフトウェア・プレプリント（DOI 付き）。
- 任意キー（未設定なら**スキップして理由を返す**）: **NASA ADS**（`ADS_API_KEY`・無料トークン・
  5,000req/日）、**Semantic Scholar**（`S2_API_KEY`。キー無しは共有枠で **HTTP 429** が返る＝実測）、
  **Web of Science Starter**（`WOS_API_KEY`・無料枠 50req/日・被引用数なし＝実測 401）。
- **arXiv は実装しない**: 本環境からは UA を変えても **HTTP 406** で取得できない（実測）。
- **JDreamIII / J-GLOBAL は実装しない**: JDreamIII は JST の有償サービス（要契約・API も別契約）、
  J-GLOBAL WebAPI も MyJ-GLOBAL 登録＋キー交付が必要。日本語文献は **J-STAGE / CiNii / JAXAリポジトリ**
  が無料・認証不要で代替する（実測で日本語クエリが機能することを確認）。

⚠️ 0件は「研究が無い」ではない（書誌 API の収録範囲は分野・年代・言語で偏る）。
⚠️ ここで返すのは**文献（書誌）**であって観測データではない。観測データは mast_observations /
alma_search / cadc_observations を使う。
"""
from __future__ import annotations

import copy
import html as _html
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, ttl_cache
from .input_utils import as_int
from .name_common import JA_ALIASES, expand_terms, resolve_object

TIMEOUT = (6, 25)
REPO = "https://github.com/loosephoto/space-finder-mcp"
UA = {"User-Agent": "space-finder-mcp/0.37 (MCP; +" + REPO + ")", "Accept": "application/json"}
# OpenAlex / Crossref の polite pool（連絡先を明示すると安定した枠が割り当てられる）
POLITE_MAILTO = "space-finder-mcp@users.noreply.github.com"
# OpenAlex の "Planetary science" 概念 ID。**裸の天体名クエリの精度が大きく上がる**
# （実測: `search=Mars` を被引用数順にすると全文一致で R 言語マニュアル（35万引用）や
# 勾配ブースティングの論文が上位に来るが、この概念で絞ると THEMIS / MSL / MAVEN が上位に来る）。
PLANETARY_CONCEPT = "C152551177"

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
NTRS = "https://ntrs.nasa.gov/api/citations/search"
NTRS_UI = "https://ntrs.nasa.gov/citations/{}"
NTRS_PDF = "https://ntrs.nasa.gov{}"
JSTAGE = "https://api.jstage.jst.go.jp/searchapi/do"
CINII = "https://cir.nii.ac.jp/opensearch/all"
JAXA_REPO = "https://jaxa.repo.nii.ac.jp/api/records"
JAXA_REPO_UI = "https://jaxa.repo.nii.ac.jp/records/{}"
ZENODO = "https://zenodo.org/api/records"
DATACITE = "https://api.datacite.org/dois"
ADS = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_UI = "https://ui.adsabs.harvard.edu/abs/{}/abstract"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
WOS = "https://api.clarivate.com/apis/wos-starter/v1/documents"

MAX_LIMIT = 25          # 1回に返す最大件数（トークン量を抑える）
MAX_ABSTRACT = 700      # 要旨の最大文字数
MAX_AUTHORS = 6         # 記録に保持する著者数

# 名前空間（J-STAGE の Atom / PRISM）
ATOM = "{http://www.w3.org/2005/Atom}"
PRISM = "{http://prismstandard.org/namespaces/basic/2.0/}"

# ソース定義: label / 出典表記 / 主言語 / 必要な環境変数（None なら認証不要）
SOURCES = {
    "openalex": {"label": "OpenAlex", "credit": "OpenAlex (CC0 1.0)", "lang": "en", "env": None},
    "crossref": {"label": "Crossref", "credit": "Crossref REST API", "lang": "en", "env": None},
    "ads": {"label": "NASA ADS", "credit": "NASA ADS (adsabs.harvard.edu)", "lang": "en",
            "env": "ADS_API_KEY"},
    "semanticscholar": {"label": "Semantic Scholar", "credit": "Semantic Scholar", "lang": "en",
                        "env": "S2_API_KEY"},
    "wos": {"label": "Web of Science", "credit": "Clarivate Web of Science Starter API", "lang": "en",
            "env": "WOS_API_KEY"},
    "ntrs": {"label": "NASA NTRS", "credit": "NASA Technical Reports Server", "lang": "en", "env": None},
    "jaxa_repo": {"label": "JAXAリポジトリ", "credit": "JAXA Research and Development Repository",
                  "lang": "ja", "env": None},
    "jstage": {"label": "J-STAGE", "lang": "ja", "env": None,
              # J-STAGE WebAPI 利用規約: 「表示情報提供元: J-STAGE」＋リンクの表示が必要
              "credit": "表示情報提供元: J-STAGE（科学技術振興機構）https://www.jstage.jst.go.jp/browse/-char/ja"},
    "cinii": {"label": "CiNii Research", "credit": "CiNii Research（国立情報学研究所）", "lang": "ja",
              "env": None},
    "zenodo": {"label": "Zenodo", "credit": "Zenodo (CERN)", "lang": "both", "env": None},
    "datacite": {"label": "DataCite", "credit": "DataCite", "lang": "both", "env": None},
}
# キー未設定でも使える日本語ソース
JA_SOURCES = ("jaxa_repo", "jstage", "cinii")
# スキップ理由の表示（キー未設定と、英語クエリを作れなかったことを混同しない）
SKIP_REASONS = {"no_key": "キー未設定", "no_english_query": "英語クエリを作れず"}
_JA_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
# 日本語 → 英語の語彙（OpenAlex / Crossref は日本語クエリを解釈できないため置換してから投げる）。
# 「長い語から先に置換」するので、複合語（大気流出・永久影 等）を必ず個別に載せる。
_TERM_EN = {
    # 太陽系天体
    "太陽系": "solar system", "系外惑星": "exoplanet", "準惑星": "dwarf planet",
    "太陽": "Sun", "水星": "Mercury", "金星": "Venus", "地球": "Earth", "火星": "Mars",
    "木星": "Jupiter", "土星": "Saturn", "天王星": "Uranus", "海王星": "Neptune",
    "冥王星": "Pluto", "月面": "lunar surface", "月": "Moon", "衛星": "satellite",
    "小惑星": "asteroid", "彗星": "comet", "惑星": "planet", "隕石": "meteorite",
    "イオ": "Io", "エウロパ": "Europa", "ガニメデ": "Ganymede", "カリスト": "Callisto",
    "タイタン": "Titan", "エンケラドス": "Enceladus", "フォボス": "Phobos", "ダイモス": "Deimos",
    "トリトン": "Triton", "ベスタ": "Vesta", "リュウグウ": "Ryugu", "イトカワ": "Itokawa",
    "ベンヌ": "Bennu", "アンドロメダ": "Andromeda",
    # 地形・プロセス
    "大気流出": "atmospheric escape", "大気圏": "atmosphere", "大気": "atmosphere",
    "磁気圏": "magnetosphere", "磁場": "magnetic field", "脱出": "escape", "流出": "loss",
    "永久影": "permanently shadowed region", "極域": "polar region", "極": "pole",
    "水氷": "water ice", "氷床": "ice sheet", "氷": "ice", "水": "water",
    "地下海": "subsurface ocean", "海": "ocean", "液体": "liquid",
    "火山活動": "volcanism", "火山": "volcano", "地震": "seismicity", "地殻": "crust",
    "マントル": "mantle", "マグマ": "magma", "衝突": "impact", "クレーター": "crater",
    "堆積物": "deposit", "砂嵐": "dust storm", "ダスト": "dust", "塵": "dust",
    "表面": "surface", "地形": "topography", "重力": "gravity",
    "有機物": "organic matter", "生命": "life", "生命探査": "biosignature", "微生物": "microbe",
    "ホスフィン": "phosphine", "硫酸": "sulfuric acid", "二酸化炭素": "carbon dioxide",
    "メタン": "methane", "酸素": "oxygen", "水素": "hydrogen", "ヘリウム": "helium",
    "大赤斑": "Great Red Spot", "雲": "cloud", "赤外線": "infrared", "電波": "radio",
    "分光": "spectroscopy", "スペクトル": "spectrum", "画像": "image",
    # 探査・観測
    "探査機": "spacecraft", "探査": "exploration", "着陸機": "lander", "着陸": "landing",
    "ローバー": "rover", "軌道": "orbit", "サンプルリターン": "sample return",
    "観測データ": "observation data", "観測": "observation", "打ち上げ": "launch",
    "ミッション": "mission", "望遠鏡": "telescope",
    # 学術一般
    "惑星科学": "planetary science", "メカニズム": "mechanism", "成因": "origin",
    "起源": "origin", "進化": "evolution", "形成": "formation", "モデル": "model",
    "シミュレーション": "simulation", "論文": "paper", "研究": "study",
}


def _as_bool(value, default: bool = False) -> bool:
    """MCP クライアントは真偽値も文字列で送るため、防御的に解釈する。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off", ""):
        return False
    return default


def _redact(text: str) -> str:
    """エラー文字列から API キーらしき部分を伏せる（クライアントへ漏らさない）。"""
    return re.sub(r"((?:api[_-]?key|token|key)=)[^&\s\"']+", r"\1***", str(text or ""))


def _strip_tags(text) -> str:
    """HTML / JATS(XML) 断片を素のテキストにする（要旨のタグ除去）。"""
    if not text:
        return ""
    s = re.sub(r"<[^>]+>", " ", _html.unescape(str(text)))
    return re.sub(r"\s+", " ", s).strip()


def _first(value) -> str:
    """文字列 / リスト（先頭が文字列）のどちらでも先頭の文字列を返す。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for v in value:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _bare_doi(value) -> Optional[str]:
    """DOI を裸の形（10.xxxx/...）に正規化する。"""
    s = _first(value)
    if not s:
        return None
    s = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", s, flags=re.I)
    return s.strip() or None


def _deinvert(inv) -> Optional[str]:
    """OpenAlex の abstract_inverted_index（語→位置リスト）を元の文へ戻す。"""
    if not isinstance(inv, dict) or not inv:
        return None
    pos = {}
    for token, idxs in inv.items():
        if isinstance(idxs, list):
            for i in idxs:
                if isinstance(i, int):
                    pos[i] = token
    if not pos:
        return None
    return " ".join(pos[k] for k in sorted(pos))


def to_english(text: str) -> dict:
    """日本語クエリを英語語へ置換する（OpenAlex / Crossref 用）。

    戻り: {"text": 置換後, "pairs": [[ja, en], ...], "untranslated": [残った日本語, ...]}
    未知語はそのまま残す（無理に訳すと検索が壊れるため）。**置換が残らない日本語クエリは
    英語圏ソースへ投げない**（無関係な文献が返る実測がある）。
    """
    s = " ".join(str(text or "").split())
    if not s:
        return {"text": "", "pairs": [], "untranslated": []}
    # 天体・衛星・探査機の和名は name_common の共通テーブル（JA_ALIASES）でも展開する
    # （「はやぶさ2」→ hayabusa2 のような固有名は _TERM_EN には無いため）
    vocab = dict(_TERM_EN)
    for ja, ens in JA_ALIASES.items():
        if ens and ja not in vocab:
            vocab[ja] = ens[0]
    out = s
    pairs = []
    for ja in sorted(vocab, key=len, reverse=True):      # 長い語から置換（複合語を優先）
        if ja in out:
            out = out.replace(ja, " " + vocab[ja] + " ")
            pairs.append([ja, vocab[ja]])
    out = " ".join(out.split())
    return {"text": out, "pairs": pairs,
            "untranslated": sorted(set(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]+", out)))}


def _rec(title="", authors=None, year=None, venue="", doi=None, url=None, citations=None,
         kind="", language="", abstract=None, source="", open_access=False, oa_url="",
         pdf_url="") -> dict:
    """正規化した文献レコード（全ソース共通の形）。"""
    names = [a for a in (authors or []) if a]
    bare = _bare_doi(doi)
    return {
        "title": _strip_tags(title),
        "authors": names[:MAX_AUTHORS],
        "author_count": len(names),
        "year": year,
        "venue": _strip_tags(venue),
        "doi": bare,
        "url": url or (("https://doi.org/" + bare) if bare else None),
        "citations": citations,
        "type": kind or "",
        "language": language or "",
        "abstract": (_strip_tags(abstract)[:MAX_ABSTRACT] or None) if abstract else None,
        "source": source,
        "source_label": SOURCES.get(source, {}).get("label", source),
        "open_access": bool(open_access),
        "oa_url": oa_url or "",
        "pdf_url": pdf_url or "",
        "also_in": [],
    }


def _get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
         accept: Optional[str] = None) -> tuple:
    """GET する。戻り: (response, エラー文字列 or None)。例外は外へ出さない。"""
    h = dict(UA)
    if accept:
        h["Accept"] = accept
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
    except requests.RequestException as ex:
        return None, "接続に失敗しました: {}".format(_redact(str(ex)[:120]))
    if r.status_code == 429:
        # 書誌 API の 429 は一時的なことが多い（実測: OpenAlex の polite pool）。1回だけ短く待って
        # 再試行する。Retry-After は必ずクランプする（NASA の 19 時間待ちのような値で固まらないため）。
        try:
            wait = float(r.headers.get("Retry-After") or 0)
        except (TypeError, ValueError):
            wait = 0.0
        time.sleep(min(max(wait, 1.0), 5.0))
        try:
            r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
        except requests.RequestException as ex:
            return None, "接続に失敗しました: {}".format(_redact(str(ex)[:120]))
    if r.status_code in (401, 403):
        return None, "認証エラー（HTTP {}）: APIキーが未設定か無効です".format(r.status_code)
    if r.status_code == 429:
        return None, "レート制限（HTTP 429）: 待つか APIキーを設定してください"
    if r.status_code == 404:
        return None, "見つかりません（HTTP 404）"
    if r.status_code >= 400:
        return None, "HTTP {} が返りました".format(r.status_code)
    return r, None


def _json(response) -> dict:
    """JSON を防御的に読む（解析失敗・dict 以外は空 dict）。"""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- ソース別アダプタ
# 各アダプタは「正規化レコードのリスト」を返し、失敗時は RuntimeError を投げる
# （呼び出し側の _search_source が捕捉して sources[].error に残す）。

def _openalex(query, limit, year_from, year_to, sort, oa_only, concept=None) -> list:
    params = {"per-page": limit, "mailto": POLITE_MAILTO, "page": 1,
              "select": ("id,doi,title,publication_year,publication_date,cited_by_count,"
                         "authorships,primary_location,best_oa_location,open_access,"
                         "abstract_inverted_index,type,language,ids")}
    # **`search=`（全文）は使わない**: 本文中の語まで一致するため、被引用数順にすると
    # 無関係な高被引用論文（例: "Mars" で R 言語マニュアル）が上位に来る（実測）。
    # タイトル・要旨に限定した title_and_abstract.search を使う。
    filters = ["title_and_abstract.search:" + query]
    if concept:
        filters.append("concepts.id:" + concept)
    if year_from:
        filters.append("from_publication_date:{}-01-01".format(int(year_from)))
    if year_to:
        filters.append("to_publication_date:{}-12-31".format(int(year_to)))
    if oa_only:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)
    params["sort"] = "cited_by_count:desc" if sort == "citations" else "relevance_score:desc"
    r, err = _get(OPENALEX, params)
    if err:
        raise RuntimeError(err)
    out = []
    for w in (_json(r).get("results") or []):
        if not isinstance(w, dict):
            continue
        pl = w.get("primary_location") or {}
        oa = w.get("open_access") or {}
        best = w.get("best_oa_location") or {}
        out.append(_rec(
            title=w.get("title") or "",
            authors=[(a.get("author") or {}).get("display_name")
                     for a in (w.get("authorships") or []) if isinstance(a, dict)],
            year=w.get("publication_year"),
            venue=((pl.get("source") or {}).get("display_name")) or "",
            doi=w.get("doi"),
            url=pl.get("landing_page_url") or (w.get("ids") or {}).get("doi") or w.get("id"),
            citations=w.get("cited_by_count"), kind=w.get("type") or "",
            language=w.get("language") or "", abstract=_deinvert(w.get("abstract_inverted_index")),
            source="openalex", open_access=bool(oa.get("is_oa")),
            oa_url=oa.get("oa_url") or "", pdf_url=best.get("pdf_url") or ""))
    return out


def _crossref(query, limit, year_from, year_to, sort, oa_only) -> list:
    # **実測**: 裸の天体名1語で `query.bibliographic`（=`query`）を使うと無関係な高被引用論文が
    # 返る（"Mars" → FinnGen の遺伝学論文）。`query.title` なら "Mars Orbiter Laser Altimeter" /
    # "OMEGA/Mars Express" / "ALH84001" のような本来の文献が返る。2語以下はタイトル検索、
    # 3語以上は書誌検索（再現率重視）にする。
    field = "query.title" if len(query.split()) <= 2 else "query.bibliographic"
    params = {field: query, "rows": limit, "mailto": POLITE_MAILTO,
              # 実測: select に language を入れると HTTP 400（select-not-available）。publisher は可。
              "select": ("DOI,title,author,issued,container-title,abstract,"
                         "is-referenced-by-count,URL,type,publisher")}
    filters = []
    if year_from:
        filters.append("from-pub-date:{}-01-01".format(int(year_from)))
    if year_to:
        filters.append("until-pub-date:{}-12-31".format(int(year_to)))
    if filters:
        params["filter"] = ",".join(filters)
    if sort == "citations":
        params["sort"], params["order"] = "is-referenced-by-count", "desc"
    r, err = _get(CROSSREF, params)
    if err:
        raise RuntimeError(err)
    out = []
    for it in ((_json(r).get("message") or {}).get("items")) or []:
        if not isinstance(it, dict):
            continue
        title = _first(it.get("title"))
        if not title:                     # 実測: title が空のレコードが混ざる
            continue
        parts = ((it.get("issued") or {}).get("date-parts") or [[]])
        year = (parts[0] or [None])[0] if isinstance(parts[0], list) else None
        out.append(_rec(
            title=title,
            authors=["{} {}".format(a.get("given") or "", a.get("family") or "").strip()
                     for a in (it.get("author") or []) if isinstance(a, dict)],
            year=year, venue=_first(it.get("container-title")),
            doi=it.get("DOI"), url=it.get("URL"),
            citations=it.get("is-referenced-by-count"), kind=it.get("type") or "",
            language="", abstract=it.get("abstract"), source="crossref"))
    return out


def _ads(query, limit, year_from, year_to, sort, oa_only) -> list:
    token = os.environ.get("ADS_API_KEY", "").strip()
    q = query
    if year_from or year_to:
        q += " year:{}-{}".format(year_from or "", year_to or "")
    params = {"q": q, "rows": limit, "start": 0,
              "fl": "title,author,year,doi,bibcode,citation_count,abstract,pub",
              "sort": "citation_count desc" if sort == "citations" else "date desc"}
    r, err = _get(ADS, params, headers={"Authorization": "Bearer " + token})
    if err:
        raise RuntimeError(err)
    out = []
    for d in ((_json(r).get("response") or {}).get("docs")) or []:
        if not isinstance(d, dict):
            continue
        bib = d.get("bibcode")
        out.append(_rec(
            title=_first(d.get("title")), authors=d.get("author") or [], year=d.get("year"),
            venue=d.get("pub") or "", doi=_first(d.get("doi")),
            url=ADS_UI.format(bib) if bib else None, citations=d.get("citation_count"),
            kind="article", language="en", abstract=d.get("abstract"), source="ads"))
    return out


def _semanticscholar(query, limit, year_from, year_to, sort, oa_only) -> list:
    token = os.environ.get("S2_API_KEY", "").strip()
    params = {"query": query, "limit": limit,
              "fields": ("title,year,abstract,authors,venue,citationCount,"
                         "externalIds,openAccessPdf,url,tldr")}
    if year_from or year_to:
        params["year"] = "{}-{}".format(year_from or "", year_to or "")
    r, err = _get(S2, params, headers={"x-api-key": token})
    if err:
        raise RuntimeError(err)
    out = []
    for p in (_json(r).get("data") or []):
        if not isinstance(p, dict):
            continue
        pdf = (p.get("openAccessPdf") or {}).get("url") or ""
        out.append(_rec(
            title=p.get("title") or "",
            authors=[a.get("name") for a in (p.get("authors") or []) if isinstance(a, dict)],
            year=p.get("year"), venue=p.get("venue") or "",
            doi=((p.get("externalIds") or {}).get("DOI")), url=p.get("url"),
            citations=p.get("citationCount"), kind="article", language="en",
            abstract=p.get("abstract") or ((p.get("tldr") or {}).get("text")),
            source="semanticscholar", open_access=bool(pdf), pdf_url=pdf))
    return out


def _wos(query, limit, year_from, year_to, sort, oa_only) -> list:
    token = os.environ.get("WOS_API_KEY", "").strip()
    q = "TS=({})".format(query)
    if year_from or year_to:
        q += " AND PY=({}-{})".format(year_from or "", year_to or "")
    params = {"q": q, "db": "WOS", "limit": limit, "page": 1,
              "sortField": "TC+D" if sort == "citations" else "PY+D"}
    r, err = _get(WOS, params, headers={"X-ApiKey": token})
    if err:
        raise RuntimeError(err)
    out = []
    for h in (_json(r).get("hits") or []):
        if not isinstance(h, dict):
            continue
        src = h.get("source") or {}
        names = (h.get("names") or {}).get("authors") or []
        out.append(_rec(
            title=h.get("title") or "",
            authors=[a.get("displayName") for a in names if isinstance(a, dict)],
            year=as_int(src.get("publishYear"), None), venue=src.get("sourceTitle") or "",
            doi=(h.get("identifiers") or {}).get("doi"),
            url=(h.get("links") or {}).get("record"),
            citations=None, kind=_first(h.get("types")), language="en", abstract=None, source="wos"))
    return out


def _ntrs_authors(value) -> list:
    """NTRS の著者フィールドを人名のリストへ分解する。

    実測: NTRS は複数著者を1つの文字列へ連結して返す（引用符で囲まれた形）。引用符で
    囲まれた名前を拾い、壊れていれば区切り文字で分割する。
    """
    s = _strip_tags(value)
    if not s:
        return []
    pairs = re.findall(r"\"([^\"]{2,})\"|'([^']{2,})'", s)
    names = [a or b for a, b in pairs]
    if not names:
        names = [x.strip() for x in re.split(r"[;／]", s) if x.strip()]
    return names


def _ntrs(query, limit, year_from, year_to, sort, oa_only) -> list:
    r, err = _get(NTRS, {"q": query, "page.size": limit, "page.from": 0})
    if err:
        raise RuntimeError(err)
    out = []
    for it in (_json(r).get("results") or []):
        if not isinstance(it, dict):
            continue
        title = it.get("title") or ""
        if not title:
            continue
        pubs = [p for p in (it.get("publications") or []) if isinstance(p, dict)]
        date = _first([p.get("publicationDate") for p in pubs]) \
            or (it.get("publicationDate") or "") or (it.get("created") or "")
        sid = _first([p.get("submissionId") for p in pubs]) \
            or ((it.get("copyright") or {}).get("submissionId")) or it.get("id")
        pdf = ""
        for d in (it.get("downloads") or []):
            if isinstance(d, dict):
                p = ((d.get("links") or {}).get("pdf")) or ""
                if p:
                    pdf = NTRS_PDF.format(p) if p.startswith("/") else p
                    break
        affs = [a for a in (it.get("authorAffiliations") or []) if isinstance(a, dict)]
        raw = " ".join(str((a.get("meta") or {}).get("author") or "") for a in affs)
        out.append(_rec(
            title=title, authors=_ntrs_authors(raw), year=as_int(str(date)[:4], None),
            venue=((it.get("center") or {}).get("name")) or "", doi=None,
            url=NTRS_UI.format(sid) if sid else None, citations=None,
            kind=it.get("stiType") or "", language="en", abstract=it.get("abstract"),
            source="ntrs", open_access=bool(pdf), pdf_url=pdf))
    return out


def _el_text(el, path: str) -> str:
    """ElementTree の要素からテキストを取り出す（無ければ空文字）。"""
    node = el.find(path)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _jstage(query, limit, year_from, year_to, sort, oa_only) -> list:
    """J-STAGE（国内学会誌）。`keyword` 検索を使う（material / article_title は ERR を返す実測）。"""
    params = {"service": 3, "keyword": query, "count": limit, "start": 1}
    if year_from:
        params["pubyearf"] = int(year_from)
    if year_to:
        params["pubyeart"] = int(year_to)
    r, err = _get(JSTAGE, params, accept="application/atom+xml")
    if err:
        raise RuntimeError(err)
    # Content-Type に charset が無いので r.text は文字化けする（実測）→ bytes を直接解釈する
    root = ET.fromstring(r.content)
    status = _el_text(root, ATOM + "result/" + ATOM + "status")
    # 実測（2026-09）: ERR_001 は「該当なし」（例: "大気流出" は ERR_001、"火星" は 159件で status=0。
    # 無意味語 "zzzqqqxyz" も ERR_001）。WARN_002 は結果を伴う警告（例: "大気" で 2,705件）。
    # したがって ERR_001 はエラーではなく 0件として扱い、ERR_001 以外の ERR_* だけをエラーにする
    # （ERR_011 は service=2 の必須パラメータ不足＝本ツールは service=3 なので該当しない）。
    if status and status.startswith("ERR") and status != "ERR_001":
        raise RuntimeError("J-STAGE status={}（検索条件を確認してください）".format(status))
    if status == "ERR_001":
        return []
    out = []
    for e in root.findall(ATOM + "entry"):
        title = _el_text(e, ATOM + "article_title/" + ATOM + "en") \
            or _el_text(e, ATOM + "article_title/" + ATOM + "ja")
        if not title:
            continue
        alt = e.find(ATOM + "link")
        url = _el_text(e, ATOM + "article_link/" + ATOM + "en") \
            or _el_text(e, ATOM + "article_link/" + ATOM + "ja") \
            or (alt.get("href") if alt is not None else None)
        journal = _el_text(e, ATOM + "material_title/" + ATOM + "en") \
            or _el_text(e, ATOM + "material_title/" + ATOM + "ja")
        out.append(_rec(
            title=title,
            authors=[n.text.strip() for n in e.findall(ATOM + "author/" + ATOM + "en/" + ATOM + "name")
                     if n.text],
            year=as_int(_el_text(e, ATOM + "pubyear"), None), venue=journal,
            doi=_el_text(e, PRISM + "doi"), url=url, citations=None, kind="article",
            language="ja", abstract=None, source="jstage"))
    return out


def _cinii(query, limit, year_from, year_to, sort, oa_only) -> list:
    """CiNii Research（国内論文・紀要）。OpenSearch の JSON を読む。"""
    r, err = _get(CINII, {"q": query, "format": "json", "count": limit})
    if err:
        raise RuntimeError(err)
    out = []
    for it in (_json(r).get("items") or []):
        if not isinstance(it, dict):
            continue
        title = it.get("title") or ""
        if not title:
            continue
        creators = it.get("dc:creator") or []
        if isinstance(creators, str):
            creators = [creators]
        date = str(it.get("prism:publicationDate") or "")
        out.append(_rec(
            title=title, authors=creators, year=as_int(date[:4], None),
            venue=it.get("prism:publicationName") or "", doi=it.get("prism:doi"),
            url=((it.get("link") or {}).get("@id")) or it.get("@id"), citations=None,
            kind=it.get("dc:type") or "", language="ja", abstract=None, source="cinii"))
    return out


def _jaxa_repo(query, limit, year_from, year_to, sort, oa_only) -> list:
    """JAXAリポジトリ（WEKO3）。日本語・英語どちらのクエリも効く（実測）。"""
    r, err = _get(JAXA_REPO, {"q": query, "size": limit})
    if err:
        raise RuntimeError(err)
    out = []
    for it in (((_json(r).get("hits") or {}).get("hits")) or []):
        if not isinstance(it, dict):
            continue
        md = it.get("metadata") or {}
        title = _first(md.get("title"))
        if not title:
            continue
        date = _first([d.get("value") for d in (md.get("date") or []) if isinstance(d, dict)]) or ""
        # 実測: JAXAリポジトリの description は「会議概要」等が先頭に来る。要旨として扱うのは
        # descriptionType が Abstract のものだけ（会議概要を要旨と偽らない）。
        abstracts = [d.get("value") for d in (md.get("description") or [])
                     if isinstance(d, dict)
                     and "abstract" in str(d.get("descriptionType") or "").lower()]
        rid = it.get("id")
        out.append(_rec(
            title=title,
            authors=list(((md.get("creator") or {}).get("creatorName")) or []),
            year=as_int(str(date)[:4], None), venue=_first(md.get("publisher")),
            doi=None, url=JAXA_REPO_UI.format(rid) if rid else None, citations=None,
            kind=_first(md.get("itemtype")), language="ja",
            abstract=_first(abstracts), source="jaxa_repo"))
    return out


def _zenodo(query, limit, year_from, year_to, sort, oa_only) -> list:
    """Zenodo（データセット・ソフトウェア・プレプリント）。"""
    r, err = _get(ZENODO, {"q": query, "size": limit, "sort": "bestmatch"})
    if err:
        raise RuntimeError(err)
    out = []
    for h in (((_json(r).get("hits") or {}).get("hits")) or []):
        if not isinstance(h, dict):
            continue
        md = h.get("metadata") or {}
        title = md.get("title") or h.get("title") or ""
        if not title:
            continue
        out.append(_rec(
            title=title,
            authors=[c.get("name") for c in (md.get("creators") or []) if isinstance(c, dict)],
            year=as_int(str(md.get("publication_date") or "")[:4], None),
            venue="Zenodo", doi=md.get("doi"), url=(h.get("links") or {}).get("self_html"),
            citations=None, kind=((md.get("resource_type") or {}).get("type")) or "",
            language=md.get("language") or "", abstract=md.get("description"),
            source="zenodo", open_access=True))
    return out


def _datacite(query, limit, year_from, year_to, sort, oa_only) -> list:
    """DataCite（DOI が付いたデータセット・論文）。"""
    r, err = _get(DATACITE, {"query": query, "page[size]": limit})
    if err:
        raise RuntimeError(err)
    out = []
    for d in (_json(r).get("data") or []):
        if not isinstance(d, dict):
            continue
        a = d.get("attributes") or {}
        title = _first([t.get("title") for t in (a.get("titles") or []) if isinstance(t, dict)])
        if not title:
            continue
        abstracts = [x.get("description") for x in (a.get("descriptions") or [])
                     if isinstance(x, dict)
                     and "abstract" in str(x.get("descriptionType") or "").lower()]
        out.append(_rec(
            title=title,
            authors=[c.get("name") for c in (a.get("creators") or []) if isinstance(c, dict)],
            year=as_int(a.get("publicationYear"), None), venue=a.get("publisher") or "",
            doi=a.get("doi"), url=a.get("url"), citations=None,
            kind=((a.get("types") or {}).get("resourceTypeGeneral")) or "",
            language=a.get("language") or "", abstract=_first(abstracts),
            source="datacite", open_access=True))
    return out


ADAPTERS = {
    "openalex": _openalex, "crossref": _crossref, "ads": _ads, "semanticscholar": _semanticscholar,
    "wos": _wos, "ntrs": _ntrs, "jstage": _jstage, "cinii": _cinii, "jaxa_repo": _jaxa_repo,
    "zenodo": _zenodo, "datacite": _datacite,
}


# ---------------------------------------------------------------- 検索の統合

def _resolve_sources(sources, ja: bool) -> tuple:
    """sources 引数を解決する。戻り: (使うソースのキー, 未知の名前)。"""
    s = " ".join(str(sources or "").split()).lower().replace("，", ",")
    if not s or s == "auto":
        keys = (list(JA_SOURCES) + ["openalex"]) if ja else ["openalex", "crossref", "ntrs"]
        # キーが設定済みの任意ソース（ADS / Semantic Scholar / WoS）は auto で有効化する
        keys += [k for k, m in SOURCES.items()
                 if m.get("env") and os.environ.get(m["env"], "").strip()]
    elif s == "all":
        keys = list(SOURCES)
    else:
        keys = [x.strip() for x in s.split(",") if x.strip()]
    unknown = [k for k in keys if k not in SOURCES]
    out = []
    for k in keys:
        if k in SOURCES and k not in out:
            out.append(k)
    return out, unknown


@ttl_cache(TTL_DAILY, maxsize=128, skip_if=lambda r: bool((r[1] or {}).get("error")))
def _search_source(source: str, query: str, limit: int, year_from, year_to, sort: str,
                   open_access: bool, concept=None) -> tuple:
    """1ソースを検索する。戻り: (レコード, ソースレポート)。例外は外へ出さない。

    日単位でキャッシュする（書誌はほぼ不変）。**エラーはキャッシュしない**（skip_if）。
    """
    meta = SOURCES[source]
    report = {"source": source, "label": meta["label"], "credit": meta["credit"], "query": query,
              "status": "ok", "count": 0, "error": None, "note": None}
    env = meta.get("env")
    if env and not os.environ.get(env, "").strip():
        report["status"] = "skipped"
        report["skip_reason"] = "no_key"
        report["note"] = ("{} が未設定のためスキップしました（MCPクライアントの env か .env に"
                          "設定すると有効になります）".format(env))
        return [], report
    try:
        # 概念フィルタ（planetary_only）は OpenAlex だけが解釈する
        if source == "openalex":
            records = ADAPTERS[source](query, limit, year_from, year_to, sort, open_access,
                                       concept=concept)
        else:
            records = ADAPTERS[source](query, limit, year_from, year_to, sort, open_access)
    except Exception as ex:            # HTTPエラー・想定外の形・解析失敗もここで止める
        report["status"] = "error"
        report["error"] = _redact(str(ex)[:200])
        return [], report
    records = [r for r in records if isinstance(r, dict) and r.get("title")]
    report["count"] = len(records)
    report["status"] = "ok" if records else "empty"
    return records, report


def _norm_title(title) -> str:
    """タイトル比較用の正規化（記号・空白除去＋小文字化）。"""
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+", "", str(title or "").lower())


def _dedupe(records: list) -> list:
    """DOI（無ければ正規化タイトル）で重複を統合する。

    呼び出し側が dict をコピーして渡す前提（**キャッシュされた戻り値を書き換えない**規約）。
    """
    out, index = [], {}
    for r in records:
        key = ("doi:" + r["doi"].lower()) if r.get("doi") else ("t:" + _norm_title(r.get("title")))
        if key == "t:":
            continue
        cur = index.get(key)
        if cur is None:
            index[key] = r
            out.append(r)
            continue
        for field in ("year", "venue", "abstract", "language", "url", "oa_url", "pdf_url", "type"):
            if not cur.get(field) and r.get(field):
                cur[field] = r[field]
        if r.get("citations") is not None and (cur.get("citations") is None
                                               or r["citations"] > cur["citations"]):
            cur["citations"] = r["citations"]
        if len(r.get("authors") or []) > len(cur.get("authors") or []):
            cur["authors"] = r["authors"]
            cur["author_count"] = r.get("author_count") or len(r["authors"])
        if cur.get("open_access") and not cur.get("oa_url") and r.get("oa_url"):
            cur["oa_url"] = r["oa_url"]
        if r.get("source") and r["source"] != cur.get("source") and r["source"] not in cur["also_in"]:
            cur["also_in"] = sorted(cur["also_in"] + [r["source"]])
    return out


def search(query, sources="auto", limit: int = 10, year_from=None, year_to=None,
           sort: str = "relevance", min_citations=None, open_access_only: bool = False,
           include_abstract: bool = True, concept=None) -> dict:
    """複数ソースを横断検索し、DOI 重複を統合した結果とソース別レポートを返す。例外は投げない。"""
    q = " ".join(str(query or "").split())
    ja = bool(_JA_RE.search(q))
    trans = to_english(q)
    usable_en = bool(trans["text"]) and not _JA_RE.search(trans["text"])
    keys, unknown = _resolve_sources(sources, ja)
    notes = []
    if ja:
        if trans["pairs"]:
            notes.append("日本語クエリを英語語へ置換して英語圏ソースへ投げました（{}）。".format(
                "、".join("{}→{}".format(a, b) for a, b in trans["pairs"][:12])))
        else:
            notes.append("日本語クエリのままでした（語彙辞書に無い語）。日本語ソース"
                         "（JAXAリポジトリ・J-STAGE・CiNii）を使ってください。")
        if trans["untranslated"]:
            notes.append("英語へ置換できなかった語: {}".format("、".join(trans["untranslated"][:8])))
    en_query = trans["text"] if usable_en else None
    jobs, skipped = [], []
    for k in keys:
        lang = SOURCES[k]["lang"]
        qq = q if lang == "ja" else (en_query or (q if lang == "both" else None))
        if not qq and lang == "en":
            skipped.append(k)
        else:
            jobs.append((k, qq or q))
    records, reports = [], []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as ex:
            futures = [(k, qq, ex.submit(_search_source, k, qq, limit, year_from, year_to,
                                         sort, open_access_only, concept)) for k, qq in jobs]
            for k, qq, fut in futures:
                try:
                    recs, rep = fut.result()
                except Exception as ex2:      # ワーカー側の想定外もここで受け止める
                    recs = []
                    rep = {"source": k, "label": SOURCES[k]["label"], "credit": SOURCES[k]["credit"],
                           "query": qq, "status": "error", "count": 0,
                           "error": _redact(str(ex2)[:200]), "note": None}
                # **キャッシュされた戻り値は呼び出し側で書き換えない**規約。`dict(r)` の浅いコピーでは
                # 入れ子のリスト（authors / also_in）がキャッシュと共有され、呼び出し側が
                # `results[0]["authors"].append(...)` すると**並行する他の呼び出しの結果まで壊れる**
                # （テストで実測）。レコードは小さく件数も最大25なので deepcopy してよい。
                records.extend(copy.deepcopy(recs))
                reports.append(dict(rep))
    for k in skipped:
        reports.append({"source": k, "label": SOURCES[k]["label"], "credit": SOURCES[k]["credit"],
                        "query": None, "status": "skipped", "count": 0, "error": None,
                        "skip_reason": "no_english_query",
                        "note": "日本語クエリを英語語へ置換できなかったため英語圏ソースを"
                                "スキップしました（語彙辞書に無い日本語クエリ）"})
    merged = _dedupe(records)
    if min_citations is not None:
        before = len(merged)
        merged = [r for r in merged if (r.get("citations") or 0) >= int(min_citations)]
        notes.append("被引用 {} 件以上で絞り込み: {} → {} 件（被引用数を持たないソース"
                     "〔JAXAリポジトリ・J-STAGE・CiNii・NTRS〕の文献は除外されます）".format(
                         int(min_citations), before, len(merged)))
    if sort == "citations":
        merged.sort(key=lambda r: (-(r.get("citations") or 0), -(r.get("year") or 0)))
    elif sort == "date":
        merged.sort(key=lambda r: (-(r.get("year") or 0), -(r.get("citations") or 0)))
    shown = merged[:limit]
    if not include_abstract:
        shown = [dict(r, abstract=None) for r in shown]
    return {"query": q, "english_query": en_query, "japanese_query": ja, "translation": trans,
            "sources": reports, "results": shown, "total_found": len(merged), "shown": len(shown),
            "returned_raw": len(records), "notes": notes, "unknown_sources": unknown,
            "no_sources": not keys}


# ---------------------------------------------------------------- 表示（content）の組み立て

def _author_line(rec: dict) -> str:
    """著者を「先頭3名 ほかN名」の形にする。"""
    names = rec.get("authors") or []
    if not names:
        return ""
    extra = (rec.get("author_count") or len(names)) - len(names)
    return "{}{}".format("、".join(names[:3]), " ほか{}名".format(extra) if extra > 0 else "")


def _result_lines(rec: dict, idx: int) -> list:
    """1件分の表示行（引用数・DOI はそのまま提示できる形で出す）。"""
    meta = []
    if rec.get("year"):
        meta.append(str(rec["year"]))
    if rec.get("venue"):
        meta.append(rec["venue"])
    if rec.get("type"):
        meta.append(rec["type"])
    if rec.get("citations") is not None:
        meta.append("被引用 {} 件".format(rec["citations"]))
    if rec.get("open_access"):
        meta.append("オープンアクセス")
    lines = ["{}. **{}**".format(idx, rec.get("title") or "(題名不明)")]
    if meta:
        lines.append("   " + " ・ ".join(meta))
    who = _author_line(rec)
    if who:
        lines.append("   👤 " + who)
    if rec.get("doi"):
        lines.append("   🔗 DOI: https://doi.org/{}".format(rec["doi"]))
    elif rec.get("url"):
        lines.append("   🔗 " + str(rec["url"]))
    if rec.get("pdf_url"):
        lines.append("   PDF: " + str(rec["pdf_url"]))
    if rec.get("abstract"):
        lines.append("   📝 要旨: {}".format(rec["abstract"]))
    if rec.get("also_in"):
        lines.append("   ↺ 他ソースにも収録: " + "、".join(
            SOURCES.get(s, {}).get("label", s) for s in rec["also_in"]))
    lines.append("   📍 取得元: {}".format(rec.get("source_label") or rec.get("source")))
    return lines


def _source_summary(reports: list) -> str:
    """ソース別の件数サマリ（スキップ・失敗も数で見えるようにする）。"""
    parts = []
    for s in reports:
        if s.get("status") in ("ok", "empty"):
            parts.append("{} {}件".format(s["label"], s["count"]))
        elif s.get("status") == "skipped":
            parts.append("{} スキップ（{}）".format(s["label"], SKIP_REASONS.get(
                s.get("skip_reason"), "理由あり")))
        else:
            parts.append("{} 失敗".format(s["label"]))
    return " / ".join(parts)


def _credits(reports: list) -> str:
    """出典表記（実際に結果を返したソースだけ）。"""
    seen, out = set(), []
    for s in reports:
        c = s.get("credit")
        if s.get("status") in ("ok", "empty") and c and c not in seen:
            seen.add(c)
            out.append(c)
    return " / ".join(out)


def _content_from_search(res: dict, title: str) -> str:
    """検索結果を人間向けのテキストにする（DOI リンクをそのまま提示できる形で出す）。"""
    reports = res["sources"]
    lines = ["📚 **{}**: {} 件（統合後 {} 件 / 生 {} 件。DOI・タイトルの重複を統合）".format(
        title, res["shown"], res["total_found"], res["returned_raw"]),
        "📊 ソース別: " + _source_summary(reports)]
    if res.get("english_query") and res.get("japanese_query"):
        lines.append("🌐 英語圏ソースへは「{}」で検索しました。".format(res["english_query"]))
    lines.append("")
    if not res["results"]:
        lines.append("該当する文献は返りませんでした。**0件は「研究が無い」ではありません**"
                     "（書誌 API の収録範囲は分野・年代・言語で偏ります）。検索語を広げるか "
                     "`sources` を変えて試してください。")
        lines.append("")
    for i, rec in enumerate(res["results"], 1):
        lines += _result_lines(rec, i)
        lines.append("")
    for s in reports:
        if s.get("status") == "error":
            lines.append("⚠️ {} は失敗: {}".format(s["label"], s.get("error")))
        elif s.get("status") == "skipped" and s.get("note"):
            lines.append("ℹ️ {}: {}".format(s["label"], s["note"]))
    for n in res.get("notes") or []:
        lines.append("ℹ️ " + n)
    if res.get("unknown_sources"):
        lines.append("⚠️ 未知のソース名（無視しました）: " + "、".join(res["unknown_sources"]))
    lines.append("")
    lines.append("出典: " + (_credits(reports) or "(結果を返したソースなし)"))
    return "\n".join(lines)


# ---------------------------------------------------------------- ツール本体

def space_literature_search(query: Optional[str] = None, sources: str = "auto",
                            limit: int = 10, year_from: Optional[int] = None,
                            year_to: Optional[int] = None, sort: str = "relevance",
                            min_citations: Optional[int] = None,
                            open_access_only: bool = False,
                            include_abstract: bool = True,
                            planetary_only: bool = False) -> CallToolResult:
    """宇宙・惑星科学の**一次文献（論文・技術報告）を横断検索して根拠を返す**ツール。

    惑星・衛星・小天体・探査ミッションについて「その記述の根拠となる文献」を DOI 付きで返します。
    Wikipedia / Wikidata の要約ではなく、**査読論文・技術報告そのもの**を根拠として提示したいときに使います。

    例:「火星の大気流出の研究」「Europa 地下海の証拠」「金星のホスフィン」「はやぶさ2 リュウグウ」
    「月の永久影の水氷」「惑星形成 シミュレーション」

    Args:
        query: 検索語（天体名・現象・ミッション名など）。**日本語可**。
        sources: 検索先。"auto"（既定・日本語クエリなら JAXAリポジトリ・J-STAGE・CiNii＋OpenAlex、
            英語クエリなら OpenAlex・Crossref・NASA NTRS）/"all"/カンマ区切りで明示。
            指定できる名前: openalex, crossref, ads, semanticscholar, wos, ntrs,
            jaxa_repo, jstage, cinii, zenodo, datacite
        limit: 返す最大件数（1〜25、既定 10）。
        year_from: 出版年の下限（例 2015）。
        year_to: 出版年の上限（例 2026）。
        sort: "relevance"（既定）/ "citations"（被引用数順）/ "date"（新しい順）。
        min_citations: 被引用数の下限。⚠️ 被引用数を持たないソース（NTRS・JAXAリポジトリ・
            J-STAGE・CiNii）の文献は除外されます。
        open_access_only: OpenAlex でオープンアクセス文献だけに絞る（既定 false）。
        include_abstract: 要旨を返すか（既定 true）。長さを抑えたいときは false。
        planetary_only: OpenAlex の検索を「惑星科学」概念（`concepts.id:C152551177`）に限定する
            （既定 false）。**裸の天体名（例「火星」）で関連性を上げたいときに true**にすると、
            全文一致で拾われる無関係な高被引用論文（実測: "Mars" で R 言語マニュアル）を排除できます。
            銀河・恒星など太陽系外の天体では false のままにしてください。

    ⚠️ 返すのは**文献（書誌）**であり観測データではありません。観測（画像・スペクトル）は
    mast_observations / alma_search / cadc_observations を使ってください。
    ⚠️ OpenAlex / Crossref は**日本語クエリを解釈できない**ため、日本語入力は語彙辞書で英語へ置換して
    から投げます（無関係な文献が返るのを防ぐため。`translation` に置換内容を入れます）。日本語文献は
    JAXAリポジトリ・J-STAGE・CiNii が得意です。
    ⚠️ APIキーが必要なソース（ADS_API_KEY / S2_API_KEY / WOS_API_KEY）は未設定ならスキップし、
    理由を `sources[].note` に残します（結果は残りのソースで返します）。

    **回答時は DOI リンクと「出典:」行をそのまま提示してください。要旨は要約せず、そのまま引用して
    ください**（図の注記と同じく、LLM が書き換えると一次情報と食い違うため）。
    """
    q = " ".join(str(query or "").split())
    if not q:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "検索語（query）を指定してください。例:「火星 大気流出」「Europa subsurface ocean」"
                "「はやぶさ2 リュウグウ」「惑星科学」"))],
            structuredContent={"error": "missing query",
                               "hint": "query には天体名・現象・ミッション名を指定します",
                               "available_sources": sorted(SOURCES)})
    lim = as_int(limit, 10, 1, MAX_LIMIT) or 10
    yf = as_int(year_from, None, 1800, 2100)
    yt = as_int(year_to, None, 1800, 2100)
    mc = as_int(min_citations, None, 0, 10 ** 7)
    srt = str(sort or "relevance").strip().lower()
    if srt not in ("relevance", "citations", "date"):
        srt = "relevance"
    ponly = _as_bool(planetary_only)
    try:
        res = search(q, sources=sources, limit=lim, year_from=yf, year_to=yt, sort=srt,
                     min_citations=mc, open_access_only=_as_bool(open_access_only),
                     include_abstract=_as_bool(include_abstract, True),
                     concept=PLANETARY_CONCEPT if ponly else None)
    except Exception as ex:            # search は投げない設計だが最後の砦（例外を外へ漏らさない）
        msg = _redact(str(ex)[:200])
        return CallToolResult(
            content=[TextContent(type="text", text="文献検索に失敗しました: " + msg)],
            structuredContent={"error": msg, "query": q})
    if res.get("no_sources"):
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "指定したソース名（{}）は使えません。使える名前: {}").format(
                    "、".join(res.get("unknown_sources") or []) or "(指定なし)",
                    "、".join(sorted(SOURCES))))],
            structuredContent={"error": "unknown sources", "query": q,
                               "unknown_sources": res.get("unknown_sources"),
                               "available_sources": sorted(SOURCES)})
    if not [s for s in res["sources"] if s["status"] in ("ok", "empty")]:
        detail = "; ".join("{}: {}".format(s["label"], s.get("error") or s.get("note"))
                          for s in res["sources"])
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="どの文献ソースからも結果を得られませんでした（{}）。".format(detail))],
            structuredContent={"error": "all sources failed", "query": q, "sources": res["sources"]})
    return CallToolResult(
        content=[TextContent(type="text", text=_content_from_search(res, "文献検索「{}」".format(q)))],
        structuredContent={
            "schema": "literature/1", "query": q, "english_query": res["english_query"],
            "japanese_query": res["japanese_query"], "translation": res["translation"],
            "planetary_only": ponly,
            "shown": res["shown"], "total_found": res["total_found"],
            "returned_raw": res["returned_raw"], "sources": res["sources"],
            "notes": res["notes"], "results": res["results"],
            "credit": _credits(res["sources"]),
            "caveat": ("書誌（論文・技術報告）です。観測データではありません（観測は "
                       "mast_observations / alma_search / cadc_observations）。"),
        })


def planetary_evidence(object_name: Optional[str] = None, limit: int = 8,
                       include_japanese: bool = True, sort: str = "citations",
                       planetary_only: bool = True) -> CallToolResult:
    """天体（惑星・衛星・小天体・探査機）の**文献的な裏づけをまとめて返す**ツール。

    1つの天体名から、①名前解決（Sesame/CDS: 和名→英語名→座標）と、②その天体に関する文献を
    **英語圏（OpenAlex・Crossref・NASA NTRS）と日本語（JAXAリポジトリ・J-STAGE・CiNii）の両方**から
    集めて、被引用数の多い順に返します。「Wikipedia の説明を、一次文献で裏づけたい／さらに深掘りしたい」
    ときに使います。

    例:「火星」「エウロパ」「タイタン」「はやぶさ2」「リュウグウ」「ベスタ」

    Args:
        object_name: 天体・ミッション名（日本語可）。
        limit: 返す最大件数（1〜25、既定 8）。
        include_japanese: 日本語文献（JAXAリポジトリ・J-STAGE・CiNii）も検索するか（既定 true）。
        sort: "citations"（既定・被引用数順）/ "date"（新しい順）/ "relevance"。
        planetary_only: OpenAlex の検索を「惑星科学」概念に限定する（既定 **true**）。裸の天体名でも
            惑星科学の文献が上位に来ます（実測: "Mars" 単独だと R 言語マニュアルが 35万引用で
            1位になるが、限定すると THEMIS / MSL / MAVEN が上位）。銀河・恒星など**太陽系外**の
            天体を調べるときは false にするか space_literature_search を使ってください。

    ⚠️ 返すのは**文献（書誌）**で、観測データではありません。観測は mast_observations /
    alma_search / cadc_observations、現在位置は solar_system_now / planetary_orbiter_track です。
    ⚠️ **太陽系天体は時刻で位置が変わるため固定座標を持ちません**。Sesame が解決できるのは
    恒星・銀河・星雲・小惑星などの固定天体です（解決できない場合はその旨を `caveats` に出し、文献
    検索は名前で続行します）。

    **回答時は DOI リンク・出典の「出典:」行・`caveats` をそのまま提示してください。**
    """
    o = " ".join(str(object_name or "").split())
    if not o:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "天体名（object_name）を指定してください。例:「火星」「エウロパ」「はやぶさ2」"
                "「リュウグウ」"))],
            structuredContent={"error": "missing object_name",
                               "hint": "object_name には天体・ミッション名を指定します"})
    lim = as_int(limit, 8, 1, MAX_LIMIT) or 8
    inc_ja = _as_bool(include_japanese, True)
    srt = str(sort or "citations").strip().lower()
    if srt not in ("relevance", "citations", "date"):
        srt = "citations"
    terms = expand_terms(o)
    tr = to_english(o)
    en_term = next((t for t in terms if not _JA_RE.search(t)), None)
    if not en_term and not _JA_RE.search(tr["text"]):
        en_term = tr["text"]
    ja_term = next((t for t in terms if _JA_RE.search(t)), None) or (o if _JA_RE.search(o) else None)
    # 名前解決（日本語入力は英語名でも試す）。例外は外へ出さない。
    hit = None
    for cand in [c for c in (o, en_term) if c]:
        try:
            hit = resolve_object(cand)
        except Exception:
            hit = None
        if hit:
            break
    ponly = _as_bool(planetary_only, True)
    concept = PLANETARY_CONCEPT if ponly else None
    # **各ソースごとに `limit` で切ってから統合すると、統合前の並べ替えで概念フィルタ済みの
    # OpenAlex 結果が落ちる**（実測: 「エウロパ」で被引用数順にすると、Crossref の同名文献
    # 〔心臓病の EUROPA 試験, 1,541引用〕が上位5件を占め、惑星科学の文献が1件も残らなかった）。
    # 候補を広めに取ってから統合・並べ替えし、最後に `limit` へ絞る。
    pool = max(lim * 2, 10)
    runs, notes = [], []
    try:
        if en_term:
            r1 = search(en_term, sources="auto", limit=pool, sort=srt,
                        include_abstract=False, concept=concept)
            runs.append(r1)
            notes += r1["notes"]
        if inc_ja and ja_term:
            r2 = search(ja_term, sources=",".join(JA_SOURCES), limit=pool, sort=srt,
                        include_abstract=False, concept=concept)
            runs.append(r2)
            notes += r2["notes"]
    except Exception as ex:        # search は投げない設計だが最後の砦
        msg = _redact(str(ex)[:200])
        return CallToolResult(content=[TextContent(type="text", text="文献検索に失敗しました: " + msg)],
                              structuredContent={"error": msg, "object_name": o})
    if not runs:
        return CallToolResult(
            content=[TextContent(type="text", text="「{}」から検索語を作れませんでした。天体名・ミッション名を指定してください。".format(o))],
            structuredContent={"error": "no query", "object_name": o})
    records, reports = [], []
    for r in runs:
        records.extend(dict(x) for x in r["results"])
        reports.extend(dict(s) for s in r["sources"])
    merged = _dedupe(records)
    if ponly:
        # 概念フィルタ（惑星科学）が効く OpenAlex を先頭に置く。他ソースは同じ概念で絞れないため、
        # 同名の無関係な文献が被引用数上位に来る（実測: 「エウロパ」→ 心臓病の EUROPA 試験が1位）。
        merged.sort(key=lambda r: (
            0 if (r.get("source") == "openalex" or "openalex" in (r.get("also_in") or [])) else 1,
            -(r.get("citations") or 0), -(r.get("year") or 0)))
    elif srt == "citations":
        merged.sort(key=lambda r: (-(r.get("citations") or 0), -(r.get("year") or 0)))
    elif srt == "date":
        merged.sort(key=lambda r: (-(r.get("year") or 0), -(r.get("citations") or 0)))
    shown = merged[:lim]
    if not [s for s in reports if s["status"] in ("ok", "empty")]:
        detail = "; ".join("{}: {}".format(s["label"], s.get("error") or s.get("note"))
                          for s in reports)
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="文献ソースから結果を得られませんでした（{}）。".format(detail))],
            structuredContent={"error": "all sources failed", "object_name": o, "sources": reports})
    caveats = []
    if hit:
        caveats.append("名前解決: 「{}」→ {}（{} / RA {:.4f}° Dec {:+.4f}°）".format(
            o, hit.get("matched_name") or hit.get("input_name") or o, hit.get("resolver"),
            hit["ra_deg"], hit["dec_deg"]))
        if hit.get("matched_via_alias"):
            caveats.append("和名→英語名「{}」として解決しました。".format(hit["matched_via_alias"]))
        if hit.get("resolver_spread_deg") and hit["resolver_spread_deg"] > 0.01:
            caveats.append("リゾルバ間で座標が最大 {:.4f}° ずれています（別天体の可能性）。".format(
                hit["resolver_spread_deg"]))
    else:
        caveats.append("Sesame(SIMBAD/NED/VizieR) で座標を解決できませんでした。"
                       "**太陽系天体は時刻で位置が変わるため固定座標を持ちません**"
                       "（軌道は solar_system_now、探査機は planetary_orbiter_track）。"
                       "文献検索は名前で実行しています。")
    caveats.append("これは文献（書誌）です。観測データ（画像・スペクトル）は mast_observations / "
                   "alma_search / cadc_observations が返します。")
    caveats.append("被引用数はソースごとに集計が異なります（Crossref と OpenAlex で値が違います）。")
    if ponly:
        caveats.append("惑星科学の概念フィルタが効く OpenAlex を上位に置いています。他のソース"
                       "（Crossref・NTRS・国内）は同じ概念で絞れないため、**同名の無関係な文献**"
                       "（実測: 「エウロパ」で心臓病の EUROPA 試験）が下位に混ざることがあります。")
    caveats.append("0件は「研究が無い」ことを意味しません（収録範囲は分野・年代・言語で偏ります）。")
    combined = {"query": o, "english_query": en_term if (en_term and _JA_RE.search(o)) else None,
                "japanese_query": bool(ja_term), "sources": reports, "results": shown,
                "total_found": len(merged), "shown": len(shown), "returned_raw": len(records),
                "notes": notes, "unknown_sources": []}
    text = _content_from_search(combined, "文献の根拠「{}」".format(o)) + "\n" + \
        "\n".join("ℹ️ " + c for c in caveats)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={
            "schema": "literature/1", "object_name": o, "search_terms": terms,
            "planetary_only": ponly,
            "english_term": en_term, "japanese_term": ja_term,
            "resolved": ({"name": hit.get("matched_name"), "ra_deg": hit.get("ra_deg"),
                          "dec_deg": hit.get("dec_deg"), "type": hit.get("otype"),
                          "resolver": hit.get("resolver"),
                          "matched_via_alias": hit.get("matched_via_alias"),
                          "resolver_spread_deg": hit.get("resolver_spread_deg")} if hit else None),
            "queries_used": [r["query"] for r in runs],
            "shown": len(shown), "total_found": len(merged), "returned_raw": len(records),
            "sources": reports, "notes": notes, "results": shown, "caveats": caveats,
            "credit": _credits(reports),
        })
