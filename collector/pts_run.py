# -*- coding: utf-8 -*-
"""引け後IR × PTS上昇 の突合と通知(設計書: docs/設計書_引け後IR_PTS通知.md)。

使い方:
    python -m collector.pts_run                    # 通常実行
    python -m collector.pts_run --no-notify        # 通知せず突合だけ
    python -m collector.pts_run --date 2026-08-27  # TDnetを過去日で実行(株探は当日ぶんのみ)

各実行が「その日の全IR」と「ランキング全体」を毎回見直す。差分方式にしないのは、
GitHub Actionsのcronが大幅に間引かれても取りこぼさないようにするため。
"""
import argparse
import json
import os
import sys
from datetime import datetime

import yaml

from collector import normalize, notify
from collector.run import load_dotenv
from collector.sources import tdnet, pts
from collector.sources.edinet import JST

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_ROOT, "config", "pts.yml")
ALERTS_DIR = os.path.join(_ROOT, "data", "pts_alerts")
SNAPSHOTS_DIR = os.path.join(_ROOT, "data", "pts_snapshots")


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return config.get("pts") or {}


def should_notify(row, config):
    """(通知するか, 通知しない理由) を返す。"""
    if row["rate"] < config.get("notify_min_rate", 3.0):
        return False, "below_rate"
    if row["turnover"] < config.get("notify_min_turnover", 1000000):
        return False, "below_turnover"
    return True, None


def build_alert(day, row, irs, now):
    """突合結果を §4.2.3 のアラートスキーマにする。"""
    rep = irs[0]  # 公表時刻が最も新しい開示を代表にする
    return {
        "id": "pts:{}:{}".format(day.isoformat(), row["code"]),
        "source": "pts",
        "category": "ir_pts",
        "title": rep["title"],
        "url": rep["url"],
        "code": row["code"],
        "company": rep["company"] or row["name"],
        "pts": row,
        "irs": irs,
        "published_at": rep["published_at"],
        "collected_at": now,
        "tags": ["引け後IR", "PTS上昇"],
    }


def build_snapshot(day, row, irs, notified, skip_reason, now):
    """§4.2.4 のスナップショット行。重複排除しないので実行のたびに1行ずつ増える。"""
    return {
        "run_at": now,
        "date": day.isoformat(),
        "code": row["code"],
        "company": (irs[0]["company"] if irs else row["name"]),
        "rate": row["rate"],
        "pts_price": row["pts_price"],
        "close": row["close"],
        "volume": row["volume"],
        "turnover": row["turnover"],
        "ir_count": len(irs),
        "ir_titles": [i["title"] for i in irs[:5]],
        "notified": notified,
        "skip_reason": skip_reason,
    }


def append_snapshots(snapshots):
    if not snapshots:
        return
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    by_month = {}
    for snap in snapshots:
        by_month.setdefault(snap["date"][:7], []).append(snap)
    for month, rows in by_month.items():
        path = os.path.join(SNAPSHOTS_DIR, "{}.jsonl".format(month))
        with open(path, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="引け後IR×PTS上昇の突合と通知")
    parser.add_argument("--no-notify", action="store_true", help="Discord通知を行わない")
    parser.add_argument("--date", default=None, help="対象日 YYYY-MM-DD(既定は今日)")
    args = parser.parse_args()

    load_dotenv()
    config = load_config()

    now_dt = datetime.now(JST)
    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        day = now_dt.date()
    now = now_dt.isoformat(timespec="seconds")

    irs = tdnet.fetch_after_close(
        day,
        ir_from=config.get("ir_from", "15:30"),
        exclude_title_contains=config.get("exclude_title_contains") or [])
    ir_total = sum(len(v) for v in irs.values())
    print("{}: 引け後IR {}件 / 対象銘柄 {}件".format(day.isoformat(), ir_total, len(irs)))
    if not irs:
        print("引け後IRが無いため株探へはアクセスしない")
        return

    rows = pts.fetch_night_ranking(
        min_rate=config.get("collect_min_rate", 1.0),
        max_pages=config.get("max_pages", 10),
        sleep_sec=config.get("page_interval_sec", 3))
    print("PTSランキング: 上昇率{}%以上 {}件".format(config.get("collect_min_rate", 1.0), len(rows)))
    if not rows:
        # 構造変更を放置すると静かに通知が止まるので、ここは失敗として扱う
        print("[warn] PTSランキングのパースに失敗した可能性(表が見つからない・0件)")
        sys.exit(1)

    joined = [(row, irs[row["code"]]) for row in rows if row["code"] in irs]

    alerts, skipped = [], []
    for row, ir_list in joined:
        ok, reason = should_notify(row, config)
        if ok:
            alerts.append(build_alert(day, row, ir_list, now))
        else:
            skipped.append((row, ir_list, reason))

    # 既に通知済みの銘柄を落とす(1銘柄1日1回)
    new_alerts = normalize.filter_new_items(alerts, ALERTS_DIR)
    new_ids = set(a["id"] for a in new_alerts)
    already = [a for a in alerts if a["id"] not in new_ids]
    new_alerts.sort(key=lambda a: a["pts"]["rate"], reverse=True)

    print("突合: {}件(通知条件を満たす {}件 / 既通知でスキップ {}件)".format(
        len(joined), len(alerts), len(already)))

    if args.no_notify:
        print("通知: スキップ(--no-notify) 対象 {}件".format(len(new_alerts)))
        notified_reason = "dry_run"
    else:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL_PTS") or os.environ.get("DISCORD_WEBHOOK_URL")
        sent = notify.send_pts_alerts(
            new_alerts, webhook,
            max_embeds=config.get("max_embeds", 10),
            max_ir=config.get("max_ir_per_embed", 3))
        print("通知: {}件".format(sent))
        notified_reason = None
        # 送信に成功してから「通知済み」にする(失敗した銘柄を永久に落とさないため)
        normalize.append_items(new_alerts, ALERTS_DIR)

    snapshots = []
    for alert in new_alerts:
        snapshots.append(build_snapshot(
            day, alert["pts"], alert["irs"],
            not args.no_notify, notified_reason, now))
    for alert in already:
        snapshots.append(build_snapshot(
            day, alert["pts"], alert["irs"], False, "already_notified", now))
    for row, ir_list, reason in skipped:
        snapshots.append(build_snapshot(day, row, ir_list, False, reason, now))
    append_snapshots(snapshots)
    print("スナップショット: {}行を追記".format(len(snapshots)))


if __name__ == "__main__":
    main()
