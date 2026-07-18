# -*- coding: utf-8 -*-
"""収集エントリポイント: 取得 -> 正規化 -> JSONL追記 -> Discord通知。

使い方:
    python -m collector.run                # 通常実行(当日+前日を取得)
    python -m collector.run --days-back 7  # 過去7日分の穴埋め
    python -m collector.run --no-notify    # 通知せず収集だけ
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import yaml

from collector import normalize, notify
from collector.sources import edinet, edinet_codes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(_ROOT, "config", "sources.yml")
ENV_PATH = os.path.join(_ROOT, ".env")


def load_dotenv(path=ENV_PATH):
    """依存を増やさないための素朴な.envローダー。既存の環境変数は上書きしない。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description="需給系開示の収集と通知")
    parser.add_argument("--days-back", type=int, default=None,
                        help="さかのぼって取得する日数(既定はconfig/sources.ymlの値)")
    parser.add_argument("--no-notify", action="store_true", help="Discord通知を行わない")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("EDINET_API_KEY")
    if not api_key:
        print("[error] EDINET_API_KEY が未設定(.env またはActionsのsecret)")
        sys.exit(1)

    with open(SOURCES_PATH, encoding="utf-8") as f:
        sources_config = yaml.safe_load(f) or {}
    days_back = args.days_back
    if days_back is None:
        days_back = int((sources_config.get("edinet") or {}).get("days_back", 2))

    code_map = edinet_codes.load_code_map()
    print("EDINETコード一覧: {}件".format(len(code_map)))

    today = datetime.now(edinet.JST).date()
    fetched = []
    for i in range(days_back):
        day = today - timedelta(days=i)
        day_items = edinet.fetch_day(day, api_key, code_map)
        print("{}: 大量保有府令 {}件".format(day.isoformat(), len(day_items)))
        fetched.extend(day_items)

    new_items = normalize.filter_new_items(fetched)

    # 新着分だけXBRL(CSV版)から株券等保有割合を取得(既知の書類には触らない)
    for item in new_items:
        doc_id = (item.get("raw") or {}).get("docID")
        if not doc_id:
            continue
        try:
            ratio, prev = edinet.fetch_holding_ratios(doc_id, api_key)
            item["ratio"] = ratio
            item["prev_ratio"] = prev
        except Exception as e:
            print("[warn] 保有割合の取得に失敗 {}: {}".format(doc_id, e))
        time.sleep(0.3)  # 書類取得APIへの連続アクセスを抑える

    normalize.append_items(new_items)
    print("新規追記: {}件".format(len(new_items)))

    rules = notify.load_rules()
    matched = []
    for item in new_items:
        rule = notify.find_matching_rule(item, rules)
        if rule is not None:
            matched.append((item, rule))
    if args.no_notify:
        print("通知対象: {}件(--no-notifyのためスキップ)".format(len(matched)))
    else:
        sent = notify.send_discord(matched, os.environ.get("DISCORD_WEBHOOK_URL"))
        print("通知: {}件".format(sent))


if __name__ == "__main__":
    main()
