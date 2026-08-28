# -*- coding: utf-8 -*-
"""株探のPTSナイトタイムセッション株価上昇率ランキングを取得する。

ランキングは上昇率の降順で返ってくるので、下限を割った時点で打ち切れる。
通常は1〜2ページで済み、株探への負荷も最小になる。
"""
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://kabutan.jp/warning/pts_night_price_increase"
# User-Agentを付けないと403が返る
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

EXPECTED_CELLS = 13  # コード/銘柄名/市場/アイコン2/終値/PTS/差/上昇率/出来高/PER/PBR/利回り
_I_CODE, _I_NAME, _I_MARKET = 0, 1, 2
_I_CLOSE, _I_PTS, _I_RATE, _I_VOLUME = 5, 6, 8, 9

_MISSING = ("", "-", "―", "－", "−", "—")


def _to_number(text):
    """セルのテキストを数値にする。取れなければNone(半端なデータで判定しないため)。"""
    if text is None:
        return None
    cleaned = text.strip()
    for ch in (",", "%", "％", "+", "＋", "円", "株", " ", "　"):
        cleaned = cleaned.replace(ch, "")
    if cleaned in _MISSING:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_ranking_html(html):
    """ランキングページのHTMLから行のリストを返す(§4.2.1のスキーマ)。

    テストから直接呼べるよう公開している。表が見つからない場合も空リストを返す。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.stock_table")
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr"):
        # 銘柄名セルは<th scope="row">なので、tdだけで取ると列が1つずれる
        cells = tr.find_all(["td", "th"])
        if len(cells) != EXPECTED_CELLS:
            continue  # ヘッダ行・広告行
        code = cells[_I_CODE].get_text(strip=True)
        name = cells[_I_NAME].get_text(strip=True)
        market = cells[_I_MARKET].get_text(strip=True)
        close = _to_number(cells[_I_CLOSE].get_text())
        pts_price = _to_number(cells[_I_PTS].get_text())
        rate = _to_number(cells[_I_RATE].get_text())
        volume = _to_number(cells[_I_VOLUME].get_text())
        if not code or not name:
            continue
        if close is None or not close or pts_price is None or rate is None or volume is None:
            print("[warn] PTSランキングの行をパースできず飛ばした: {}".format(code or "?"))
            continue
        rows.append({
            "code": code,
            "name": name,
            "market": market,
            "close": close,
            "pts_price": pts_price,
            "rate": rate,
            "volume": int(volume),
            "turnover": int(pts_price * volume),
        })
    return rows


def fetch_night_ranking(min_rate=1.0, max_pages=10, sleep_sec=3):
    """PTS上昇率がmin_rate以上の行を上昇率の降順で返す。"""
    rows = []
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(sleep_sec)  # robots.txt の Crawl-delay: 3 を守る
        res = requests.get(BASE_URL, params={
            "market": "0",
            "capitalization": "-1",
            "dispmode": "normal",
            "stc": "",
            "stm": "0",
            "page": str(page),
        }, headers={"User-Agent": USER_AGENT}, timeout=30)
        res.raise_for_status()
        page_rows = parse_ranking_html(res.text)
        if not page_rows:
            break  # 表が空 = 最終ページを超えた
        for row in page_rows:
            if row["rate"] < min_rate:
                return rows  # 降順なので以降は全部下回る
            rows.append(row)
    else:
        print("[warn] {}ページで打ち切った(上昇銘柄が多い日)".format(max_pages))
    return rows
