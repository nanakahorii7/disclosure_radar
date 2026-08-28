# -*- coding: utf-8 -*-
"""やのしんWEB-API経由でTDnetの適時開示を取得する(認証不要)。

引け後(既定15:30以降)の開示だけを4桁コード別にまとめて返すのが本モジュールの役割。
閾値やフィルタ語彙は引数で受け取り、ここには判定ロジックを持たない。
"""
try:
    from html import unescape          # Python 3.4+
except ImportError:                       # pragma: no cover
    from HTMLParser import HTMLParser
    unescape = HTMLParser().unescape

import requests

API_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{}.json"
LIMIT = 1000  # 1日の適時開示は150〜300件程度なので1リクエストに収まる


def fetch_after_close(day, ir_from="15:30", exclude_title_contains=None):
    """day(date, JST)の適時開示のうち ir_from 以降のものを {4桁コード: [開示, ...]} で返す。

    各開示は §4.2.2 のスキーマ。同一銘柄の開示は公表時刻の降順に並ぶ。
    """
    url = API_URL.format(day.strftime("%Y%m%d"))
    res = requests.get(url, params={"limit": LIMIT}, timeout=30)
    res.raise_for_status()
    body = res.json()

    # やのしんAPIは常に "YYYY-MM-DD HH:MM:SS" の固定長で返すので文字列比較で足りる
    threshold = "{} {}:00".format(day.strftime("%Y-%m-%d"), ir_from)
    excludes = exclude_title_contains or []

    result = {}
    for wrapper in body.get("items") or []:
        doc = wrapper.get("Tdnet") or {}
        pubdate = (doc.get("pubdate") or "").strip()
        if not pubdate or pubdate < threshold:
            continue
        code5 = (doc.get("company_code") or "").strip()
        if len(code5) < 5:
            continue  # ETF・投資法人などコードを持たない開示
        # やのしんAPIは "Q&amp;A" のようにHTMLエンティティのまま返す
        title = unescape((doc.get("title") or "").strip())
        if any(word in title for word in excludes):
            continue
        doc_id = (doc.get("id") or "").strip()
        if not doc_id:
            continue
        item = {
            "id": "tdnet:{}".format(doc_id),
            "code": code5[:4],
            "company": unescape((doc.get("company_name") or "").strip()),
            "title": title,
            "url": doc.get("document_url") or "",
            "published_at": "{}T{}+09:00".format(pubdate[:10], pubdate[11:19]),
        }
        result.setdefault(item["code"], []).append(item)

    for code in result:
        result[code].sort(key=lambda i: i["published_at"], reverse=True)
    return result
