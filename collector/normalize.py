# -*- coding: utf-8 -*-
"""共通スキーマのアイテムをJSONL(data/items/YYYY-MM.jsonl)に追記する。重複排除もここで行う。"""
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITEMS_DIR = os.path.join(_ROOT, "data", "items")


def _month_of(item):
    published = item.get("published_at") or item.get("collected_at") or ""
    return published[:7] if len(published) >= 7 else "unknown"


def _month_path(month):
    return os.path.join(ITEMS_DIR, "{}.jsonl".format(month))


def load_month(month):
    path = _month_path(month)
    if not os.path.exists(path):
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def append_new_items(items):
    """既存JSONLに無いidのアイテムだけ追記し、追記したアイテムのリストを返す。"""
    months = sorted(set(_month_of(i) for i in items))
    known_ids = set()
    for month in months:
        for existing in load_month(month):
            known_ids.add(existing.get("id"))

    new_items = []
    seen = set()
    for item in sorted(items, key=lambda i: i.get("published_at") or ""):
        item_id = item.get("id")
        if not item_id or item_id in known_ids or item_id in seen:
            continue
        seen.add(item_id)
        new_items.append(item)

    if new_items:
        os.makedirs(ITEMS_DIR, exist_ok=True)
        by_month = {}
        for item in new_items:
            by_month.setdefault(_month_of(item), []).append(item)
        for month, month_items in by_month.items():
            with open(_month_path(month), "a", encoding="utf-8") as f:
                for item in month_items:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return new_items
