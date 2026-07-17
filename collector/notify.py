# -*- coding: utf-8 -*-
"""通知ルール(config/rules.yml)の評価とDiscord Webhookへの送信。"""
import os
import time

import requests
import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(_ROOT, "config", "rules.yml")

MAX_EMBEDS = 10  # Discordの1メッセージ上限

_CATEGORY_LABEL = {
    "large_holding": ("\U0001F40B", "大量保有報告書", 0x1E88E5),   # 🐋 青
    "change_report": ("\U0001F504", "変更報告書", 0xFB8C00),       # 🔄 橙
    "buyback": ("\U0001F4B0", "自社株買い", 0x43A047),             # 💰 緑
    "tob": ("\U0001F4E3", "TOB", 0xE53935),                        # 📣 赤
    "news": ("\U0001F4F0", "ニュース", 0x757575),                  # 📰 灰
}


def load_rules(path=RULES_PATH):
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return config.get("rules") or []


def find_matching_rule(item, rules):
    """アイテムに最初にマッチしたルールを返す。どれにもマッチしなければNone。"""
    for rule in rules:
        if _match_one(item, rule.get("match") or {}):
            return rule
    return None


def _match_one(item, cond):
    categories = cond.get("category")
    if categories and item.get("category") not in categories:
        return False
    title = item.get("title") or ""
    contains = cond.get("title_contains")
    if contains and not any(word in title for word in contains):
        return False
    not_contains = cond.get("title_not_contains")
    if not_contains and any(word in title for word in not_contains):
        return False
    # 数値条件: 保有割合が取れていない書類(None)は不成立=通知しない(フェイルセーフ)
    if "prev_ratio_min" in cond:
        prev = item.get("prev_ratio")
        if prev is None or prev < cond["prev_ratio_min"]:
            return False
    if "ratio_below" in cond:
        ratio = item.get("ratio")
        if ratio is None or ratio >= cond["ratio_below"]:
            return False
    return True


def send_discord(items, webhook_url):
    """新着アイテムをDiscordに通知する。上限を超える分は件数だけ知らせる。"""
    if not items:
        return 0
    if not webhook_url:
        print("[warn] DISCORD_WEBHOOK_URL未設定のため通知をスキップ({}件)".format(len(items)))
        return 0

    embeds = [_to_embed(item) for item in items[:MAX_EMBEDS]]
    payload = {"embeds": embeds}
    rest = len(items) - MAX_EMBEDS
    if rest > 0:
        payload["content"] = "ほか{}件の新着があります(タイムラインで確認)".format(rest)

    res = requests.post(webhook_url, json=payload, timeout=30)
    if res.status_code not in (200, 204):
        raise RuntimeError("Discord通知に失敗: HTTP {} {}".format(res.status_code, res.text[:200]))
    time.sleep(1)  # 連続実行時のレート制限対策
    return len(items)


def _to_embed(item):
    emoji, label, color = _CATEGORY_LABEL.get(item.get("category"), ("\U0001F4C4", "開示", 0x757575))
    company = item.get("company")
    code = item.get("code")
    if company and code:
        subject = "[{}] {}".format(code, company)
    else:
        subject = company or "銘柄不明"

    lines = ["**{}**".format(subject)]
    if item.get("filer"):
        lines.append("提出者: {}".format(item["filer"]))
    ratio = item.get("ratio")
    if ratio is not None:
        prev = item.get("prev_ratio")
        if prev is not None:
            lines.append("保有割合: {:.2f}% (前回 {:.2f}%)".format(ratio, prev))
        else:
            lines.append("保有割合: {:.2f}% (新規)".format(ratio))
    published = item.get("published_at") or ""
    if published:
        lines.append("提出時刻: {}".format(published[11:16] or published))

    title = "{} {} | {}".format(emoji, label, item.get("title") or "")
    return {
        "title": title[:250],
        "url": item.get("url"),
        "description": "\n".join(lines)[:2000],
        "color": color,
    }
