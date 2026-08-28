# -*- coding: utf-8 -*-
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import normalize, notify, pts_run
from collector.sources import pts

import datetime

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "pts_night_20260827.html")
CONFIG = pts_run.load_config()
DAY = datetime.date(2026, 8, 27)
NOW = "2026-08-27T20:00:11+09:00"


def _fixture_rows():
    with io.open(FIXTURE, encoding="utf-8") as f:
        return pts.parse_ranking_html(f.read())


def _ir(code=u"9832", title=u"通期連結業績予想の修正に関するお知らせ", hhmm=u"17:00"):
    return {
        "id": "tdnet:1278069",
        "code": code,
        "company": u"オートバックス",
        "title": title,
        "url": "https://example.invalid/doc.pdf",
        "published_at": "2026-08-27T{}:00+09:00".format(hhmm),
    }


# ---- パース ----

def test_ランキングを15件パースできる():
    rows = _fixture_rows()
    assert len(rows) == 15


def test_先頭行が実データと一致する():
    row = _fixture_rows()[0]
    assert row == {
        "code": "7743", "name": u"シード", "market": u"東Ｓ",
        "close": 635.0, "pts_price": 735.0, "rate": 15.75,
        "volume": 1100, "turnover": 808500,
    }


def test_上昇率の降順で返る():
    rows = _fixture_rows()
    assert all(rows[i]["rate"] >= rows[i + 1]["rate"] for i in range(len(rows) - 1))


def test_売買代金はPTS株価かける出来高():
    for row in _fixture_rows():
        assert row["turnover"] == int(row["pts_price"] * row["volume"])


_DECIMAL_HTML = u"""<table class="stock_table st_market"><tbody>
<tr><td><a href="/stock/?code=6085">6085</a></td><th scope="row">アーキテクツ</th>
<td>東Ｇ</td><td></td><td></td><td>201</td><td>209.6</td>
<td><span class="up">+8.6</span></td><td><span class="up">+4.28</span>%</td>
<td>483,700</td><td>635</td><td>－</td><td>－</td></tr>
</tbody></table>"""


def test_小数のPTS株価も扱える():
    row = pts.parse_ranking_html(_DECIMAL_HTML)[0]
    assert row["pts_price"] == 209.6
    assert row["turnover"] == 101383520


def test_英字入りコードもそのまま通る():
    html = _DECIMAL_HTML.replace(u">6085<", u">462A<")
    assert pts.parse_ranking_html(html)[0]["code"] == "462A"


def test_表が無ければ空リスト():
    assert pts.parse_ranking_html(u"<html><body><p>なし</p></body></html>") == []


def test_数値パースの境界():
    assert pts._to_number(u"1,100") == 1100.0
    assert pts._to_number(u"+15.75%") == 15.75
    assert pts._to_number(u"－") is None
    assert pts._to_number(u"") is None
    assert pts._to_number(None) is None


# ---- 通知判定 ----

def _row(rate, pts_price, volume, code="9832"):
    return {"code": code, "name": u"テスト", "market": u"東Ｐ", "close": 1000.0,
            "pts_price": pts_price, "rate": rate, "volume": volume,
            "turnover": int(pts_price * volume)}


def test_上昇率が閾値未満なら通知しない():
    ok, reason = pts_run.should_notify(_row(2.99, 1000, 10000), CONFIG)
    assert ok is False and reason == "below_rate"


def test_売買代金が閾値未満なら通知しない():
    # 実データの7743: +15.75%だが代金80万円 -> 流動性で落とす
    ok, reason = pts_run.should_notify(_row(15.75, 735, 1100), CONFIG)
    assert ok is False and reason == "below_turnover"


def test_両方満たせば通知する():
    ok, reason = pts_run.should_notify(_row(3.49, 1601, 2900), CONFIG)
    assert ok is True and reason is None


# ---- アラート ----

def test_アラートIDは日付と銘柄で一意():
    alert = pts_run.build_alert(DAY, _row(3.49, 1601, 2900), [_ir()], NOW)
    assert alert["id"] == "pts:2026-08-27:9832"
    assert alert["category"] == "ir_pts"


def test_会社名は株探の略称ではなくTDnet側を使う():
    row = _row(3.49, 1601, 2900)
    row["name"] = u"オートバクス"  # 株探側の略称
    alert = pts_run.build_alert(DAY, row, [_ir()], NOW)
    assert alert["company"] == u"オートバックス"


# ---- Discord embed(銘柄名・PTS上昇率・IRタイトルが入ること) ----

def test_embedに銘柄名と上昇率とIRタイトルが入る():
    alert = pts_run.build_alert(DAY, _row(3.49, 1601, 2900), [_ir()], NOW)
    embed = notify._to_pts_embed(alert)
    assert u"オートバックス" in embed["title"]
    assert u"9832" in embed["title"]
    assert u"+3.49%" in embed["description"]
    assert u"通期連結業績予想の修正に関するお知らせ" in embed["description"]
    assert u"17:00" in embed["description"]


def test_embedのIRは最大3件で残りは件数表示():
    irs = [_ir(title=u"開示{}".format(i), hhmm=u"1{}:00".format(i)) for i in range(5)]
    alert = pts_run.build_alert(DAY, _row(3.49, 1601, 2900), irs, NOW)
    embed = notify._to_pts_embed(alert, max_ir=3)
    assert u"…ほか2件の開示" in embed["description"]
    assert u"開示3" not in embed["description"]


def test_小数のPTS株価は小数のまま表示される():
    alert = pts_run.build_alert(DAY, _row(4.28, 209.6, 483700), [_ir()], NOW)
    embed = notify._to_pts_embed(alert)
    assert u"PTS 209.6円" in embed["description"]


# ---- 保存先の分離 ----

def test_items_dirを渡すと別ディレクトリに保存される(tmpdir):
    target = str(tmpdir)
    alert = pts_run.build_alert(DAY, _row(3.49, 1601, 2900), [_ir()], NOW)
    assert normalize.filter_new_items([alert], target) == [alert]
    normalize.append_items([alert], target)
    # 2回目は既存idとして落ちる = 1銘柄1日1回
    assert normalize.filter_new_items([alert], target) == []
    with io.open(os.path.join(target, "2026-08.jsonl"), encoding="utf-8") as f:
        saved = json.loads(f.readline())
    assert saved["id"] == "pts:2026-08-27:9832"


def test_既定の保存先は従来どおりdata_items():
    assert normalize._month_path("2026-08").endswith(
        os.path.join("data", "items", "2026-08.jsonl"))
