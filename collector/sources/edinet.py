# -*- coding: utf-8 -*-
"""EDINET API v2 から大量保有府令(大量保有報告書・変更報告書・訂正)の書類を取得する。"""
import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOC_FETCH_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{}"
DOC_URL = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{}"
ORDINANCE_LARGE_HOLDING = "060"  # 大量保有府令

# XBRL-CSV内の株券等保有割合(共同保有分を含む合計はコンテキストID=FilingDateInstant)
_ELEM_RATIO = "jplvh_cor:HoldingRatioOfShareCertificatesEtc"
_ELEM_RATIO_PREV = "jplvh_cor:HoldingRatioOfShareCertificatesEtcPerLastReport"
_CONTEXT_TOTAL = "FilingDateInstant"

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
        "ratio": None,       # 株券等保有割合% (新着確定後にXBRLから取得)
        "prev_ratio": None,  # 直前の報告書の保有割合%
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


def fetch_holding_ratios(doc_id, api_key):
    """書類取得API(type=5: XBRLのCSV版)から株券等保有割合を取り出す。

    (今回の保有割合%, 前回の保有割合% or None) を返す。取得できなければ (None, None)。
    """
    res = requests.get(
        DOC_FETCH_URL.format(doc_id),
        params={"type": "5", "Subscription-Key": api_key},
        timeout=60,
    )
    res.raise_for_status()
    if "application/json" in (res.headers.get("content-type") or ""):
        return (None, None)  # CSVが用意されていない書類

    # コンテキストID -> 値。共同保有の場合は合計行(FilingDateInstant)があるが、
    # 単独保有者の書類は保有者別の行(FilingDateInstant_..._FilerLargeVolumeHolderNMember)しか無い。
    ratios = {}
    prevs = {}
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    for name in zf.namelist():
        if not name.lower().endswith(".csv"):
            continue
        text = zf.read(name).decode("utf-16", errors="replace")
        for row in csv.reader(io.StringIO(text), delimiter="\t"):
            if len(row) < 9 or not row[2].startswith(_CONTEXT_TOTAL):
                continue
            if row[0] == _ELEM_RATIO:
                ratios[row[2]] = _to_percent(row[8])
            elif row[0] == _ELEM_RATIO_PREV:
                prevs[row[2]] = _to_percent(row[8])
    return (_resolve_total(ratios), _resolve_total(prevs))


def _resolve_total(by_context):
    """合計行があればその値、無ければ保有者別の値(複数なら合算=共同保有の合計)を返す。"""
    if _CONTEXT_TOTAL in by_context:
        return by_context[_CONTEXT_TOTAL]
    values = [v for v in by_context.values() if v is not None]
    if not values:
        return None
    return round(sum(values), 2)


def _to_percent(value):
    """XBRLの比率(例: '0.3929')を%(39.29)に変換する。"""
    try:
        return round(float(value) * 100, 2)
    except (TypeError, ValueError):
        return None


def _parse_submit_time(text):
    """'2026-07-10 15:32' 形式をISO 8601(JST)に変換する。"""
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=JST).isoformat(timespec="seconds")
