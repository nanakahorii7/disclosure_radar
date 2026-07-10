# -*- coding: utf-8 -*-
"""EDINET API v2 から大量保有府令(大量保有報告書・変更報告書・訂正)の書類を取得する。"""
from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOC_URL = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{}"
ORDINANCE_LARGE_HOLDING = "060"  # 大量保有府令

JST = timezone(timedelta(hours=9))


def fetch_day(day, api_key, code_map):
    """指定日(date)の書類一覧から大量保有府令の書類を共通スキーマで返す。"""
    params = {
        "date": day.strftime("%Y-%m-%d"),
        "type": "2",
        "Subscription-Key": api_key,
    }
    res = requests.get(API_URL, params=params, timeout=30)
    res.raise_for_status()
    body = res.json()
    status = (body.get("metadata") or {}).get("status")
    if status not in (None, "200", 200):
        raise RuntimeError("EDINET書類一覧APIエラー: {}".format(body.get("metadata")))

    items = []
    for doc in body.get("results") or []:
        if doc.get("ordinanceCode") != ORDINANCE_LARGE_HOLDING:
            continue
        item = _to_item(doc, code_map)
        if item is not None:
            items.append(item)
    return items


def _to_item(doc, code_map):
    doc_id = doc.get("docID")
    if not doc_id:
        return None
    desc = doc.get("docDescription") or ""
    filer = (doc.get("filerName") or "").strip()

    if "変更報告書" in desc:
        category = "change_report"
    else:
        category = "large_holding"
    tags = ["大量保有"]
    if "変更報告書" in desc:
        tags.append("変更")
    if "訂正" in desc:
        tags.append("訂正")

    issuer = code_map.get(doc.get("issuerEdinetCode") or "") or {}

    published = _parse_submit_time(doc.get("submitDateTime"))
    return {
        "id": "edinet:{}".format(doc_id),
        "source": "edinet",
        "category": category,
        "title": "{}({})".format(desc or "大量保有関連書類", filer or "提出者不明"),
        "url": DOC_URL.format(doc_id),
        "code": issuer.get("sec_code"),
        "company": issuer.get("name"),
        "filer": filer or None,
        "published_at": published,
        "collected_at": datetime.now(JST).isoformat(timespec="seconds"),
        "tags": tags,
        "raw": {
            "docID": doc_id,
            "docTypeCode": doc.get("docTypeCode"),
            "formCode": doc.get("formCode"),
            "edinetCode": doc.get("edinetCode"),
            "secCode": doc.get("secCode"),
            "issuerEdinetCode": doc.get("issuerEdinetCode"),
            "docDescription": desc,
        },
    }


def _parse_submit_time(text):
    """'2026-07-10 15:32' 形式をISO 8601(JST)に変換する。"""
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=JST).isoformat(timespec="seconds")
