"""ロケット打ち上げ情報 (Launch Library 2 / The Space Devs, 認証なし)。"""
from __future__ import annotations

from typing import Optional

import requests

LL2 = "https://ll.thespacedevs.com/2.3.0"

def upcoming_launches(limit: int = 5) -> str:
    """今後予定されているロケット打ち上げの一覧を返す。

    Args:
        limit: 返す件数（既定 5）。
    """
    r = requests.get(f"{LL2}/launches/upcoming/", params={"limit": limit}, timeout=25)
    r.raise_for_status()
    d = r.json()
    if not d.get("results"):
        return "予定されている打ち上げが見つかりませんでした。"
    lines = ["今後のロケット打ち上げ:"]
    for l in d["results"]:
        name = l.get("name", "?")
        window = (l.get("window_start") or "")[:16].replace("T", " ")
        status = l.get("status", {}).get("name", "?")
        pad = l.get("pad", {}).get("location", {}).get("name", "?") if l.get("pad") else "?"
        lines.append(f"- {name}  {window} UTC  [{status}]  {pad}")
    return "\n".join(lines)
