"""tools/list に載せる description を短くする（リポジトリ側の docstring は完全なまま残す）。

なぜ必要か
----------
MCP クライアントは `tools/list` の description を**毎ターンのプロンプトに載せる**。
ツールが 66 本に増えた時点で description の合計は 44,963 文字（日本語は UTF-8 で
1文字3バイトなので実測 118KB の `tools/list`）になり、モデルの入力トークンと
生成速度を直接押し下げる。一方で description には **LLM が正しく呼ぶために必要な
情報**（引数の意味・受け付ける値・「figure.notes はそのまま引用する」等の契約）が
入っていて、単純に削ると誤用が増える。

そこで「要約（先頭段落）＋例の行＋Args ブロック＋契約文」だけを残し、**背景説明の
長い段落を落とす**。Args ブロックは引数の一次情報なので**一字も変えずに**残す
（機械的に途中で切ると括弧が閉じない壊れた文になる。実測で確認済み）。

実装上の約束
------------
- 元の docstring（関数の `__doc__`）は書き換えない。リポジトリを読む人・エージェントは
  従来どおり完全な説明を読める。
- 何も落とせない docstring（短いもの）は、そのまま返す。
- 引数の一覧が `Args:` 以外の見出しでも落とさない（`引数:` / `Arguments:`）。
"""
from __future__ import annotations

import re

# 落とすと契約違反になる文（メディアのリンク提示・figure.notes の引用）は必ず残す。
_CONTRACT_KEYS = ("そのまま提示", "要約・言い換えせず", "要約せず", "引用すること",
                  "figure.notes")
# 見出しの揺れ（Args / 引数 / Arguments）
_ARG_HEADS = ("Args:", "引数:", "Arguments:")


def _blocks(doc: str) -> list:
    """docstring を空行区切りのブロックに分ける（共通インデントを落とす）。"""
    text = (doc or "").replace("\r\n", "\n")
    lines = [ln[4:] if ln.startswith("    ") else ln for ln in text.split("\n")]
    blocks, cur = [], []
    for ln in lines:
        if ln.strip() == "":
            if cur:
                blocks.append("\n".join(cur).rstrip())
                cur = []
        else:
            cur.append(ln.rstrip())
    if cur:
        blocks.append("\n".join(cur).rstrip())
    return blocks


def _summary(block: str, cap: int = 300) -> str:
    """先頭ブロックを要約として使う（長いときは最初の2文まで）。"""
    if len(block) <= cap:
        return block.strip()
    parts = re.split(r"(?<=。)", block)
    out = "".join(parts[:2]).strip()
    return out or block[:cap].strip()


def compact_description(doc: str, cap: int = 300) -> str:
    """docstring から「要約＋例＋Args＋契約文」を作る（元の docstring は変更しない）。"""
    blocks = _blocks(doc)
    if not blocks:
        return ""
    out = [_summary(blocks[0], cap)]
    # ツール選択の手がかりになる例（「例:」で始まる行）
    examples = [ln.strip() for b in blocks for ln in b.split("\n")
                if ln.strip().startswith("例")]
    if examples:
        out.append("\n".join(examples))
    # 引数の説明は一次情報なので一字も変えずに残す。`Args:` が単独ブロックの先頭に
    # 無い docstring（例の行の直後に続くもの）でも落とさない（実測 2026-10-31:
    # uk_stac_collections は「例:」と同じブロックに Args があり、配信から消えていた）。
    for b in blocks:
        lines_ = b.split("\n")
        for i, ln in enumerate(lines_):
            if ln.strip().startswith(_ARG_HEADS):
                out.append("\n".join(lines_[i:]).strip())
                return_block = True
                break
        else:
            continue
        break
    # 契約文（メディアのリンク提示・figure.notes の引用）を文単位で拾う
    for b in blocks:
        for ln in b.split("\n"):
            s = ln.strip()
            if s and any(k in s for k in _CONTRACT_KEYS) and s not in out:
                out.append(s)
    return "\n\n".join(x for x in out if x)


def slim_schema(node):
    """JSON Schema から意味を変えない冗長さだけを落とす（`tools/list` の軽量化）。

    FastMCP（pydantic）は各プロパティに `title`（＝プロパティ名と同じ）を付け、
    `Optional[...]` を `anyOf: [X, {"type": "null"}]` で表す。どちらも**意味は同じ**で、
    `type: ["string", "null"]` と省略可能プロパティの `default: null` は冗長。
    実測: 66本で `tools/list` が 118,677 → ... バイトに減る（description の圧縮と合わせて）。
    """
    if isinstance(node, dict):
        out = {}
        for key, val in node.items():
            if key == "title":
                continue
            if key == "default" and val is None:
                continue
            out[key] = slim_schema(val)
        any_of = out.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            types = [x.get("type") for x in any_of if isinstance(x, dict)]
            if "null" in types:
                other = [x for x in any_of if x.get("type") != "null"]
                if len(other) == 1 and isinstance(other[0], dict):
                    base = dict(other[0])
                    base.update({k: v for k, v in out.items() if k != "anyOf"})
                    base["type"] = [base["type"], "null"] if base.get("type") else "null"
                    return base
        return out
    if isinstance(node, list):
        return [slim_schema(x) for x in node]
    return node
