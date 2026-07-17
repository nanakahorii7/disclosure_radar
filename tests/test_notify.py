# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import notify

NEW_RULE = {
    "name": u"新規の大量保有報告書(5%超え)のみ通知",
    "match": {"category": ["large_holding"], "title_not_contains": [u"訂正"]},
}
EXIT_RULE = {
    "name": u"大口の売り抜け(5%割れ)",
    "label": u"売り抜け",
    "emoji": u"\U0001F4C9",
    "color": 0xD32F2F,
    "match": {
        "category": ["change_report"],
        "title_not_contains": [u"訂正"],
        "prev_ratio_min": 5,
        "ratio_below": 5,
    },
}
RULES = [NEW_RULE, EXIT_RULE]


def _change_report(ratio, prev_ratio, title=u"変更報告書(テスト商会)"):
    return {
        "category": "change_report",
        "title": title,
        "ratio": ratio,
        "prev_ratio": prev_ratio,
    }


def test_exit_matches_5pct_breakdown():
    item = _change_report(4.07, 5.02)
    assert notify.find_matching_rule(item, RULES) is EXIT_RULE


def test_prev_exactly_5_matches():
    # prev_ratio_min は「以上」
    item = _change_report(4.9, 5.0)
    assert notify.find_matching_rule(item, RULES) is EXIT_RULE


def test_ratio_exactly_5_does_not_match():
    # ratio_below は「未満」
    item = _change_report(5.0, 6.0)
    assert notify.find_matching_rule(item, RULES) is None


def test_small_decrease_above_5_does_not_match():
    item = _change_report(6.5, 7.0)
    assert notify.find_matching_rule(item, RULES) is None


def test_missing_ratio_never_matches():
    # 保有割合が取れなかった書類はフェイルセーフで通知しない
    assert notify.find_matching_rule(_change_report(None, 6.0), RULES) is None
    assert notify.find_matching_rule(_change_report(4.0, None), RULES) is None


def test_correction_excluded():
    item = _change_report(4.0, 6.0, title=u"訂正報告書(変更報告書)(テスト商会)")
    assert notify.find_matching_rule(item, RULES) is None


def test_large_holding_rule_still_matches():
    item = {
        "category": "large_holding",
        "title": u"大量保有報告書(テスト商会)",
        "ratio": 5.2,
        "prev_ratio": None,
    }
    assert notify.find_matching_rule(item, RULES) is NEW_RULE


def test_embed_uses_rule_display_override():
    item = _change_report(3.76, 5.06)
    item.update({"code": "2432", "company": u"ディー・エヌ・エー", "filer": u"テスト"})
    embed = notify._to_embed(item, EXIT_RULE)
    assert embed["title"].startswith(u"\U0001F4C9 売り抜け | ")
    assert embed["color"] == 0xD32F2F
    assert u"保有割合: 3.76% (前回 5.06%)" in embed["description"]


def test_embed_without_rule_keeps_category_default():
    item = _change_report(3.76, 5.06)
    embed = notify._to_embed(item)
    assert embed["title"].startswith(u"\U0001F504 変更報告書 | ")
