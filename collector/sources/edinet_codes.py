# -*- coding: utf-8 -*-
"""EDINETコード一覧(提出者名・証券コードの解決用)の取得とキャッシュ。

金融庁配布のEdinetcode.zipをdata/にキャッシュし、7日ごとに更新する。
git checkoutでファイルのmtimeが変わるため、鮮度はメタファイルに記録した日付で判定する。
"""
import csv
import io
import json
import os
import zipfile
from datetime import date

import requests

CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
REFRESH_DAYS = 7

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_CSV = os.path.join(_ROOT, "data", "edinet_codes.csv")
CACHE_META = os.path.join(_ROOT, "data", "edinet_codes.meta.json")


def _is_fresh():
    if not (os.path.exists(CACHE_CSV) and os.path.exists(CACHE_META)):
        return False
    try:
        with open(CACHE_META, encoding="utf-8") as f:
            meta = json.load(f)
        fetched = date.fromisoformat(meta.get("fetched", "1970-01-01"))
    except (ValueError, OSError, json.JSONDecodeError):
        return False
    return (date.today() - fetched).days < REFRESH_DAYS


def _download():
    res = requests.get(CODELIST_URL, timeout=60)
    res.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    csv_name = None
    for name in zf.namelist():
        if name.lower().endswith(".csv"):
            csv_name = name
            break
    if csv_name is None:
        raise RuntimeError("Edinetcode.zip にCSVが見つからない")
    text = zf.read(csv_name).decode("cp932", errors="replace")
    os.makedirs(os.path.dirname(CACHE_CSV), exist_ok=True)
    with open(CACHE_CSV, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    with open(CACHE_META, "w", encoding="utf-8") as f:
        json.dump({"fetched": date.today().isoformat()}, f)


def load_code_map():
    """EDINETコード -> {"name": 提出者名, "sec_code": 4桁銘柄コード or None} の辞書を返す。

    取得に失敗しても古いキャッシュがあればそれを使う。何も無ければ空辞書。
    """
    if not _is_fresh():
        try:
            _download()
        except Exception as e:
            print("[warn] EDINETコード一覧の更新に失敗: {}".format(e))
    if not os.path.exists(CACHE_CSV):
        return {}

    code_map = {}
    with open(CACHE_CSV, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    # 1行目はメタ情報、2行目がヘッダー
    if len(rows) < 3:
        return {}
    header = rows[1]
    try:
        i_edinet = header.index("ＥＤＩＮＥＴコード")
        i_name = header.index("提出者名")
        i_sec = header.index("証券コード")
    except ValueError:
        print("[warn] EDINETコード一覧CSVのヘッダーが想定と異なる")
        return {}
    for row in rows[2:]:
        if len(row) <= max(i_edinet, i_name, i_sec):
            continue
        edinet_code = row[i_edinet].strip()
        if not edinet_code:
            continue
        sec = row[i_sec].strip()
        # 証券コードは5桁(末尾0)で入っているので4桁に正規化
        sec_code = sec[:4] if len(sec) == 5 and sec.endswith("0") else (sec or None)
        code_map[edinet_code] = {"name": row[i_name].strip(), "sec_code": sec_code}
    return code_map
