# 引け後IR × PTS上昇 通知機能 設計書

disclosure_radar への機能追加。東証の大引け後に出た適時開示(TDnet)のうち、
その夜のPTSナイトタイムセッションで株価が上昇している銘柄をDiscordに通知する。

- 作成日: 2026-08-27
- 対象読者: この設計書を読んで実装するAIエージェント
- 前提: 実装者はこの設計書と、リポジトリ内の既存コード以外の文脈を持たない
- 追加先リポジトリ: `~/Claude_Project/disclosure_radar`(既存の `docs/DESIGN.md` はEDINET収集の設計。本書はその追加機能の設計であり、DESIGN.md を置き換えない)

---

## 1. 目的と背景

ユーザー(個人投資家)は、翌営業日に見るべき銘柄を**前日の夜のうちに**知りたい。
東証の大引け(15:30)後に出たIRは、翌朝までニュースサイトを見て回らないと気づけないが、
PTS(私設取引システム)のナイトタイムセッションでは16:30〜23:59に売買が続いており、
**市場参加者がそのIRをどう評価したかが値段として先に出る**。

したがって本機能が捉えるのは「IRが出た」ではなく「**IRが出て、かつ実際に買われている**」である。
IRだけなら1日100件以上出るが、そのうち夜のうちに買われるのは数銘柄しかない。
この積集合を取ることが、通知量を実用的な水準に抑える仕掛けそのものになっている。

**この通知は売買サインではない。**「明日の朝、チャートと材料を見に行く銘柄リスト」である。
売買ルールはユーザーの裁量なので、本機能はエントリー・エグジットの判定を一切行わない。
迷ったときは「通知を増やして判断材料を出す」よりも「**通知を減らして確度の高いものだけ残す**」側に倒すこと
(zaraba_watchで確立した方針と同じ)。

## 2. 用語定義

| 用語 | 定義 |
|---|---|
| TDnet | 東証の適時開示情報伝達システム。上場企業のIR(決算・業績修正・提携・自社株買い等)の一次ソース |
| 適時開示 / IR | TDnetに公表される開示。本書では同義で使う |
| 引け後IR | TDnetの公表時刻(`pubdate`)が当日 **15:30:00 以降** の開示。15:30ちょうどは含む |
| PTS | 私設取引システム。ここではSBIジャパンネクスト証券のPTSを指す |
| ナイトタイムセッション | PTSの夜間立会。**16:30〜23:59**。本機能が見るのはこの時間帯の値段だけ |
| PTS上昇率 | `(PTS株価 - 当日の東証終値) / 当日の東証終値 × 100`。株探の「通常取引◯日終値比」列の%値と同じ。**単位は%、小数第2位まで** |
| PTS売買代金 | `PTS株価 × PTS出来高`。単位は円。株探は代金列を持たないので自前で計算する |
| 4桁コード | `7203` のような東証の銘柄コード。株探の表記。英字を含む場合もある(`462A`) |
| 5桁コード | `72030` のようなTDnet/EDINETの表記。**4桁コードの末尾に1文字足したもの**。`154A` → `154A0` |

## 3. スコープ

### やること

- TDnetから当日の適時開示を取得し、15:30以降のものを抽出する
- 株探のPTSナイトタイム株価上昇率ランキングを取得する
- 両者を4桁コードで突合し、閾値を満たした銘柄をDiscordに通知する
- 突合結果を(通知しなかったものも含めて)JSONLに保存する
- 上記をGitHub Actionsで平日3回自動実行する

### やらないこと

- **売買判断・スコアリング・順位付けの独自ロジック**を作らない。並べ替えはPTS上昇率の降順のみ
- **バックテスト・検証スクリプトを実装しない**(§9。データを貯めるところまで)
- **閲覧UI(Webページ)を作らない**。したがって本書には「API設計」「UI設計」の章は無い
- PTSデイタイムセッション、東証の日中足、板情報は扱わない
- 既存のEDINET収集(`collector/run.py`、`.github/workflows/collect.yml`、`config/rules.yml`)の
  **挙動を変えない**。新しいワークフロー・新しいエントリポイントとして並置する
- 個別銘柄ページ(`kabutan.jp/stock/?code=XXXX`)を銘柄ごとに叩かない(§13)

## 4. データ設計

### 4.1 データソース一覧

| ソース | 取得方法 | 認証 | 制約・注意 |
|---|---|---|---|
| TDnet適時開示 | `GET https://webapi.yanoshin.jp/webapi/tdnet/list/{YYYYMMDD}.json?limit=1000` | 不要 | やのしんWEB-APIの非公式ミラー。1日の開示は150〜300件程度なので `limit=1000` で1回のリクエストに収まる。日付は**JSTの日付**を自分で計算して埋める |
| PTSナイトタイム上昇率 | `GET https://kabutan.jp/warning/pts_night_price_increase?market=0&capitalization=-1&dispmode=normal&stc=&stm=0&page={N}` | 不要 | HTMLスクレイピング。**User-Agentヘッダ必須**(無いと403)。`robots.txt` は本パスを許可しているが `Crawl-delay: 3` があるので**ページ間に3秒のsleepを必ず入れる**。1ページ15件、上昇銘柄が150件を超えるとページが増える |

どちらもAPIキー不要なので、既存の `EDINET_API_KEY` は本機能では使わない。

**JSONレスポンスの実例**(TDnet、1件ぶん抜粋):

```json
{"Tdnet": {
  "id": "1278069",
  "pubdate": "2026-08-27 17:00:00",
  "company_code": "98320",
  "company_name": "オートバックス",
  "title": "特別損失の計上および法人税等調整額（益）の計上ならびに通期連結業績予想の修正に関するお知らせ",
  "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120260827526995.pdf",
  "markets_string": "東証"
}}
```

**PTSランキングのHTML行構造**(実物。これが唯一の正解なので、この並びを前提に実装する):

```html
<tr>
  <td class="tac"><a href="/stock/?code=7743">7743</a></td>   <!-- [0] コード -->
  <th scope="row" class="tal">シード</th>                      <!-- [1] 銘柄名 (th!) -->
  <td class="tac">東Ｓ</td>                                    <!-- [2] 市場 -->
  <td class="gaiyou_icon">...</td>                             <!-- [3] アイコン(空) -->
  <td class="chart_icon">...</td>                              <!-- [4] アイコン(空) -->
  <td>635</td>                                                 <!-- [5] 東証終値 -->
  <td>735</td>                                                 <!-- [6] PTS株価 -->
  <td class="w61"><span class="up">+100</span></td>            <!-- [7] 差 -->
  <td class="w50"><span class="up">+15.75</span>%</td>         <!-- [8] 上昇率 -->
  <td>1,100</td>                                               <!-- [9] PTS出来高 -->
  <td>16.5</td><td>1.17</td><td>－</td>                        <!-- [10-12] PER/PBR/利回り -->
</tr>
```

**銘柄名のセルは `<th>` であって `<td>` ではない。** `find_all("td")` だけで取ると列がずれる。
必ず `find_all(["td", "th"])` で13セルとして扱うこと。

### 4.2 スキーマ定義

#### 4.2.1 PTSランキング行(`pts.fetch_night_ranking()` の戻り値要素)

| フィールド | 型 | null | 意味 | 実例 |
|---|---|---|---|---|
| `code` | str | 不可 | 4桁コード(英字含む場合あり) | `"7743"` |
| `name` | str | 不可 | 銘柄名(株探表記、略称) | `"シード"` |
| `market` | str | 不可 | 市場区分(株探表記) | `"東Ｓ"` |
| `close` | float | 不可 | 当日の東証終値(円) | `635.0` |
| `pts_price` | float | 不可 | PTS株価(円。小数あり) | `735.0` |
| `rate` | float | 不可 | PTS上昇率(%) | `15.75` |
| `volume` | int | 不可 | PTS出来高(株) | `1100` |
| `turnover` | int | 不可 | PTS売買代金(円)= `int(pts_price * volume)` | `808500` |

いずれかがパースできなかった行は**その行ごと捨てる**(ログに警告を出す)。半端なデータで判定しない。

#### 4.2.2 引け後IR(`tdnet.fetch_after_close()` の戻り値要素)

| フィールド | 型 | null | 意味 | 実例 |
|---|---|---|---|---|
| `id` | str | 不可 | `"tdnet:" + Tdnet.id` | `"tdnet:1278069"` |
| `code` | str | 不可 | 4桁コード(`company_code[:4]`) | `"9832"` |
| `company` | str | 不可 | 会社名 | `"オートバックス"` |
| `title` | str | 不可 | 開示タイトル | `"通期連結業績予想の修正に関するお知らせ"` |
| `url` | str | 不可 | `document_url` をそのまま | `"https://webapi.yanoshin.jp/rd.php?..."` |
| `published_at` | str | 不可 | ISO 8601(JST)。`pubdate` に `+09:00` を付ける | `"2026-08-27T17:00:00+09:00"` |

#### 4.2.3 アラート(`data/pts_alerts/YYYY-MM.jsonl`、通知したものだけ)

既存の `collector/normalize.py` の共通スキーマに寄せる。

| フィールド | 型 | 意味 | 実例 |
|---|---|---|---|
| `id` | str | `"pts:{YYYY-MM-DD}:{4桁コード}"`。**これが1銘柄1日1回の担保** | `"pts:2026-08-27:9832"` |
| `source` | str | 固定 `"pts"` | `"pts"` |
| `category` | str | 固定 `"ir_pts"` | `"ir_pts"` |
| `title` | str | 代表IRのタイトル(公表時刻が最も新しいもの) | `"通期連結業績予想の修正に関するお知らせ"` |
| `url` | str | 代表IRのURL | `"https://webapi.yanoshin.jp/rd.php?..."` |
| `code` / `company` | str | 4桁コード / 会社名(株探の `name` ではなくTDnetの `company_name` を使う) | `"9832"` / `"オートバックス"` |
| `pts` | object | PTSランキング行そのもの(§4.2.1) | `{"rate": 3.49, ...}` |
| `irs` | array | 引け後IRの配列(§4.2.2)。公表時刻の降順 | `[{...}, {...}]` |
| `published_at` | str | 代表IRの公表時刻(ISO 8601 JST) | `"2026-08-27T17:00:00+09:00"` |
| `collected_at` | str | 実行時刻(ISO 8601 JST) | `"2026-08-27T20:00:11+09:00"` |
| `tags` | array | 固定 `["引け後IR", "PTS上昇"]` | |

#### 4.2.4 スナップショット(`data/pts_snapshots/YYYY-MM.jsonl`、閾値未満も含めて全件)

**重複排除しない。追記のみ。**同じ銘柄が同じ日に3回(実行のたびに)記録されるのが正しい
— 時刻ごとの値動きを後から追えるようにするためである(§9)。

| フィールド | 型 | 意味 | 実例 |
|---|---|---|---|
| `run_at` | str | 実行時刻(ISO 8601 JST) | `"2026-08-27T20:00:11+09:00"` |
| `date` | str | 対象日(JST) | `"2026-08-27"` |
| `code` / `company` | str | 4桁コード / 会社名 | `"9832"` / `"オートバックス"` |
| `rate` / `pts_price` / `close` / `volume` / `turnover` | 数値 | §4.2.1と同じ | |
| `ir_count` | int | 引け後IRの件数 | `2` |
| `ir_titles` | array[str] | 引け後IRのタイトル(最大5件) | `["特別損失の計上...", "特定子会社の異動..."]` |
| `notified` | bool | この実行で通知したか | `true` |
| `skip_reason` | str or null | 通知しなかった理由。`"below_rate"` / `"below_turnover"` / `"already_notified"` / `"dry_run"`(`--no-notify`実行) / `null` | `null` |

### 4.3 データのエッジケース

| ケース | 挙動 |
|---|---|
| 休場日(土日祝)に実行された | TDnetは0件、PTSランキングも前営業日の残骸か空。**IRが0件なら突合結果も必ず0件**になるので、休場日判定のコードは書かない(祝日カレンダーを持たない) |
| PTSランキングが1件も無い時間帯 | 表が空 or 「該当する銘柄はありません」。0件として正常終了する。**例外にしない** |
| 上昇率が `+1.0%` 以上の銘柄が150件を超える | `max_pages: 10` で打ち切る。ログに「打ち切り」を出す |
| PTS株価に小数がある | `209.6` のように出る。`float` で扱う。売買代金は `int(pts_price * volume)` に丸める |
| PER/PBR/利回りが `－`(全角ハイフン) | 本機能では使わない列なのでパースしない |
| TDnetの `company_code` が英字入り | `"154A0"` → `[:4]` で `"154A"`。株探側も `"462A"` の形なので**そのまま突合できる** |
| TDnetの `company_code` が4桁以下 or 空 | その開示を捨てる(ETF・REITの一部、投資法人など) |
| 同じ銘柄に引け後IRが複数 | 1つのアラートにまとめる。`irs` に全部入れ、Discordには**最大3件**まで表示し、残りは「ほかN件」 |
| PTSランキングに載っているがTDnetにIRが無い | 対象外(通知もスナップショットもしない)。IR起因でない値動きは本機能の関心外 |
| 東証終値が0 or 欠損 | その行を捨てる(上昇率が計算不能なため) |
| 同じ日に複数回実行 | アラートJSONLの `id` 重複で2回目以降は通知されない。スナップショットは毎回追記される |

## 5. コアロジック仕様

### 5.1 全体の流れ(`collector/pts_run.py`)

```
1. .env を読む(既存の collector.run.load_dotenv を再利用)
2. today = 現在時刻(JST)の日付
3. irs = tdnet.fetch_after_close(today, ir_from="15:30", exclude=設定値)
   -> {4桁コード: [IR, ...]} の辞書
4. if len(irs) == 0: 「引け後IRなし」とログを出して正常終了(exit 0)
5. rows = pts.fetch_night_ranking(min_rate=1.0, max_pages=10)
6. joined = [(row, irs[row.code]) for row in rows if row.code in irs]
   -> 上昇率の降順(ランキングの並び順のまま)
7. スナップショットを data/pts_snapshots/YYYY-MM.jsonl に全件追記
8. 通知候補 = joined のうち rate >= 3.0 かつ turnover >= 1_000_000
9. 既存アラートJSONLに id が無いものだけ残す(既存 normalize.filter_new_items を使う)
10. Discordへ送信(上位10件、超過分は件数だけ本文に書く)
11. data/pts_alerts/YYYY-MM.jsonl に追記
12. サマリーを標準出力に出す
```

手順4で早期終了するのは、**IRが1件も無ければ株探に一切アクセスしない**ため。
休場日に無駄なスクレイピングをしないという意図がある。

### 5.2 引け後IRの抽出(`tdnet.fetch_after_close`)

```python
def fetch_after_close(day, ir_from="15:30", exclude_title_contains=None):
    """day(date型, JST)の適時開示のうち、ir_from以降のものを4桁コード別に返す。"""
    url = "https://webapi.yanoshin.jp/webapi/tdnet/list/{}.json?limit=1000".format(
        day.strftime("%Y%m%d"))
    body = requests.get(url, timeout=30).json()   # raise_for_status を先に呼ぶ
    threshold = "{} {}:00".format(day.strftime("%Y-%m-%d"), ir_from)  # "2026-08-27 15:30:00"

    result = {}
    for wrapper in body.get("items") or []:
        d = wrapper.get("Tdnet") or {}
        pubdate = d.get("pubdate") or ""
        if pubdate < threshold:          # 文字列比較でよい(固定長のISO風フォーマット)
            continue
        code5 = (d.get("company_code") or "").strip()
        if len(code5) < 5:
            continue
        code = code5[:4]
        title = d.get("title") or ""
        if any(word in title for word in (exclude_title_contains or [])):
            continue
        result.setdefault(code, []).append({...})   # §4.2.2 のスキーマ
    for code in result:
        result[code].sort(key=lambda i: i["published_at"], reverse=True)
    return result
```

`pubdate` の文字列比較で足りるのは、やのしんAPIが常に `"YYYY-MM-DD HH:MM:SS"` の固定長で返すため。
日付をまたぐ判定は行わない(当日ぶんのエンドポイントしか叩かないので、翌0時以降の開示は入ってこない)。

**除外タイトル(`exclude_title_contains` の既定値)**:
`["訂正", "決算補足説明資料", "コーポレート・ガバナンスに関する報告書", "独立役員届出書", "内部統制報告書"]`

このリストが短いのは意図的である。PTSで買われていること自体が強力なフィルタなので、
IR側で「重要そうな開示」を選別すると取りこぼす。実際 2026-08-27 には
「株主・投資家の皆様からのお問い合わせについてのご回答」という一見ノイズの開示が出た銘柄(4840)が
PTSで **+15.5%** 買われていた。**リストを増やしたくなったら、まずスナップショットで実績を見ること。**

### 5.3 PTSランキングの取得(`pts.fetch_night_ranking`)

```python
BASE_URL = "https://kabutan.jp/warning/pts_night_price_increase"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def fetch_night_ranking(min_rate=1.0, max_pages=10, sleep_sec=3):
    """PTS上昇率がmin_rate以上の行を、上昇率の降順で返す。"""
    rows = []
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(sleep_sec)          # robots.txt の Crawl-delay: 3 を守る
        html = _get(BASE_URL, params={
            "market": "0", "capitalization": "-1", "dispmode": "normal",
            "stc": "", "stm": "0", "page": str(page)})
        page_rows = _parse(html)
        if not page_rows:
            break                          # 表が空 = 最終ページを超えた
        for row in page_rows:
            if row["rate"] < min_rate:
                return rows                # 降順ソートなので以降は全部下回る -> 打ち切り
            rows.append(row)
    return rows
```

ランキングは上昇率の降順で返ってくるので、`min_rate` を下回った時点で残りを見る必要がない。
これで通常は**1〜2ページ(4秒程度)**で済み、株探への負荷も最小になる。

`_parse` の要点:

```python
soup = BeautifulSoup(html, "html.parser")
table = soup.select_one("table.stock_table")     # ヘッダの指数表(id=header_shisuu_big)ではない
for tr in table.select("tbody tr"):
    cells = tr.find_all(["td", "th"])            # th(銘柄名)を含めて13セル
    if len(cells) != 13:
        continue                                 # ヘッダ行・広告行を飛ばす
    code = cells[0].get_text(strip=True)
    name = cells[1].get_text(strip=True)
    market = cells[2].get_text(strip=True)
    close = _num(cells[5]); pts_price = _num(cells[6])
    rate = _num(cells[8]); volume = _int(cells[9])
```

`_num` はセルのテキストから `,` `%` `＋` `+` を除いて `float` にし、`－`(全角)や空文字は `None` を返す。
`None` が1つでもあればその行を捨てる。

### 5.4 通知判定

```python
NOTIFY_MIN_RATE = 3.0          # %
NOTIFY_MIN_TURNOVER = 1000000  # 円

def should_notify(row):
    if row["rate"] < NOTIFY_MIN_RATE:
        return False, "below_rate"
    if row["turnover"] < NOTIFY_MIN_TURNOVER:
        return False, "below_turnover"
    return True, None
```

売買代金の下限を置くのは、夜間PTSの板が極端に薄いためである。数百株の売買で十数%動く銘柄は
翌朝の東証で同じ値段が付かない。**上昇率だけで判定してはいけない。**

### 5.5 入出力の実例(検算用の固定ケース)

**2026-08-27 20:00 実行**を想定した実データでの期待結果。実装後、この日のデータで検算すること
(TDnetは日付指定で過去を取れる。PTSランキングは当日ぶんしか取れないので、
株探側は `tests/fixtures/pts_night_20260827.html` の固定HTMLで代用する)。

TDnet: 当日167件、うち15:30以降が **122件**。
PTSランキング(上昇率+3.0%以上): 8銘柄。突合と判定は以下のとおり。

| コード | 銘柄 | 上昇率 | PTS株価 | 出来高 | 売買代金 | 引け後IR | 判定 |
|---|---|---|---|---|---|---|---|
| 7743 | シード | +15.75% | 735 | 1,100 | 808,500円 | 0件 | 突合せず(IRなし) |
| 4840 | トライアイズ | +15.50% | 745 | 1,700 | 1,266,500円 | 1件 | **通知** |
| 3987 | エコモット | +15.22% | 757 | 5,200 | 3,936,400円 | 0件 | 突合せず(IRなし) |
| 3907 | シリコンスタ | +12.41% | 2,400 | 34,200 | 82,080,000円 | 0件 | 突合せず(IRなし) |
| 6522 | アスタリスク | +4.40% | 2,680 | 67,900 | 181,972,000円 | 1件 | **通知** |
| 3692 | (略) | +4.37% | 5,855 | 10,300 | 60,306,500円 | 0件 | 突合せず(IRなし) |
| 6085 | (略) | +4.28% | 209.6 | 483,700 | 101,383,520円 | 0件 | 突合せず(IRなし) |
| 9832 | オートバックス | +3.49% | 1,601 | 2,900 | 4,642,900円 | **2件** | **通知**(2件を1通にまとめる) |

→ **通知は3件**(4840 / 6522 / 9832)、スナップショットは3行。
7743 は上昇率1位だが引け後IRが無いので対象外 — これが「IR起因の値動きだけを見る」という設計の核心である。
なお 7743 は仮に IR があっても売買代金80万円で `below_turnover` により通知されない。

## 6. アーキテクチャとファイル構成

既存のEDINET収集(`collector/run.py` + `collect.yml`)とは**独立したパイプライン**にする。
実行時間帯も処理内容も違うため、相乗りさせると20分ごとにPTS処理が走ってしまう。
共有するのは「Discord送信」と「JSONL追記」の下回りだけである。

```
disclosure_radar/
├── collector/
│   ├── pts_run.py                    ★新規 エントリポイント(python -m collector.pts_run)
│   ├── normalize.py                  ☆変更 items_dir引数を追加(後方互換)
│   ├── notify.py                     ☆変更 ir_ptsカテゴリと send_pts_alerts を追加
│   ├── run.py                        (変更しない。load_dotenv だけ import して再利用)
│   └── sources/
│       ├── tdnet.py                  ★新規 やのしんWEB-APIからTDnet適時開示
│       └── pts.py                    ★新規 株探PTSナイトタイムランキングのスクレイピング
├── config/
│   └── pts.yml                       ★新規 閾値・実行パラメータ
├── data/
│   ├── pts_alerts/YYYY-MM.jsonl      ★新規 通知したアラート(重複排除あり)
│   └── pts_snapshots/YYYY-MM.jsonl   ★新規 突合結果の全件(重複排除なし)
├── tests/
│   ├── test_pts.py                   ★新規
│   └── fixtures/pts_night_20260827.html  ★新規 パース検証用の固定HTML
├── .github/workflows/pts.yml         ★新規 平日3回の実行
├── requirements.txt                  ☆変更 beautifulsoup4 を追加
└── docs/設計書_引け後IR_PTS通知.md    (本書)
```

### 6.1 各ファイルの責務と公開関数

**`collector/sources/tdnet.py`**
- `fetch_after_close(day, ir_from="15:30", exclude_title_contains=None) -> dict[str, list[dict]]`
  引け後IRを4桁コード別に返す(§5.2)
- HTTP以外の判定ロジックを持たない。閾値は引数で受け取る

**`collector/sources/pts.py`**
- `fetch_night_ranking(min_rate=1.0, max_pages=10, sleep_sec=3) -> list[dict]`
  PTS上昇率降順の行リストを返す(§5.3)
- `parse_ranking_html(html) -> list[dict]` — テストから直接呼べるよう**公開する**

**`collector/pts_run.py`**
- `main()` — argparse で `--no-notify`(通知せず突合だけ)、`--date YYYY-MM-DD`(TDnetを過去日で実行)を受ける
- `load_config()` — `config/pts.yml` を読む
- 例外を握りつぶさない。株探・TDnetのHTTPエラーはそのまま送出して**Actionsを失敗させる**

**`collector/normalize.py`(変更)**
既存の3関数に `items_dir` 引数を後方互換で足すだけ。既定値は現在の `ITEMS_DIR`。

```python
def load_month(month, items_dir=None): ...
def filter_new_items(items, items_dir=None): ...
def append_items(items, items_dir=None): ...
```

`items_dir` が `None` のときは従来どおり `data/items` を使う。**既存の呼び出し側(`run.py`)は一切変更しない。**

**`collector/notify.py`(変更)**
1. `_CATEGORY_LABEL` に1行足す: `"ir_pts": ("\U0001F680", "引け後IR×PTS上昇", 0xFF6D00)`(🚀 オレンジ)
2. 新関数 `send_pts_alerts(alerts, webhook_url) -> int` を追加する。
   既存の `send_discord` / `_to_embed` は**触らない**(大量保有通知の表示が変わると困る)

Discord embed の書式:

```
🚀 引け後IR×PTS上昇 | [9832] オートバックス
  PTS 1,601円 (+3.49% / 東証終値 1,547円)
  売買代金 464万円 ・ 出来高 2,900株
  17:00 特別損失の計上および法人税等調整額（益）の計上ならびに通期連結業績予想の修正に関するお知らせ
  17:00 特定子会社の異動（株式譲渡）に関するお知らせ
```
- `title`: `"🚀 引け後IR×PTS上昇 | [{code}] {company}"`(250文字で切る)
- `url`: 代表IR(最新)のURL
- `description`: 上記2〜4行目。IRは最大3件、超過分は `…ほかN件` を1行足す。2000文字で切る
- `color`: `0xFF6D00`
- 1メッセージ10 embed まで(既存 `MAX_EMBEDS` と同じ)。超過分は `content` に `ほかN件` と書く

**`config/pts.yml`**

```yaml
pts:
  ir_from: "15:30"              # 引け後の開始時刻(JST)
  collect_min_rate: 1.0         # スナップショットに残す上昇率の下限(%)
  notify_min_rate: 3.0          # 通知する上昇率の下限(%)
  notify_min_turnover: 1000000  # 通知する売買代金の下限(円)
  max_pages: 10                 # 株探ランキングの最大取得ページ数
  page_interval_sec: 3          # ページ間のsleep(robots.txt の Crawl-delay)
  max_embeds: 10                # 1メッセージあたりのDiscord embed上限
  max_ir_per_embed: 3           # 1銘柄あたりDiscordに表示するIR件数
  exclude_title_contains:
    - 訂正
    - 決算補足説明資料
    - コーポレート・ガバナンスに関する報告書
    - 独立役員届出書
    - 内部統制報告書
```

### 6.2 ライブラリ

`requirements.txt` に `beautifulsoup4>=4.9,<5` を追加する(既存の `requests` / `urllib3<2` / `PyYAML` はそのまま)。
`lxml` は入れない(ビルドが必要でActionsが遅くなる)。パーサは標準の `html.parser` を使う。

### 6.3 GitHub Actions ワークフロー(`.github/workflows/pts.yml`)

```yaml
name: pts

on:
  schedule:
    - cron: "45 8 * * 1-5"    # JST 17:45 平日
    - cron: "0 11 * * 1-5"    # JST 20:00 平日
    - cron: "0 14 * * 1-5"    # JST 23:00 平日
  workflow_dispatch: {}

concurrency:
  group: pts
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  pts:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install --quiet -r requirements.txt
      - name: 突合・通知
        env:
          DISCORD_WEBHOOK_URL_PTS: ${{ secrets.DISCORD_WEBHOOK_URL_PTS }}
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: python -m collector.pts_run
      - name: データをcommit
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/pts_alerts data/pts_snapshots
          if ! git diff --cached --quiet; then
            git commit -m "PTS突合結果 ($(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M') JST)"
            git pull --rebase origin master
            git push
          else
            echo "新着なし"
          fi
```

cronはUTC。JST 17:45/20:00/23:00 はいずれもUTCで同じ日付の 08:45/11:00/14:00 になるので、
曜日指定 `1-5` はそのままJSTの月〜金と一致する(日付をまたがない)。

**GitHub Actionsのcronは大幅に間引かれる**(このリポジトリの実測で平均68分間隔、1日5回)。
本設計はそれを前提に、**各実行が「その日の全IR」と「現在のランキング全体」を毎回見直す**ようにしてある。
17:45の実行が飛んでも20:00の実行が同じ銘柄を拾うので、**遅れることはあっても取りこぼさない。**
これが「差分だけを見る」実装にしてはいけない理由である。

既存の `collect` ワークフローも `git add data/` で同じディレクトリをcommitする。
両者が同時に走ってもpush前に `git pull --rebase` するので破綻しないが、
**`pts` ジョブでは `git add data/pts_alerts data/pts_snapshots` とパスを限定する**こと
(EDINET収集の途中結果を巻き込まないため)。

## 7. 環境変数

| 変数 | 用途 | 未設定時 |
|---|---|---|
| `DISCORD_WEBHOOK_URL_PTS` | PTS通知の送信先 | `DISCORD_WEBHOOK_URL` にフォールバック |
| `DISCORD_WEBHOOK_URL` | 既存(大量保有通知) | 両方未設定なら通知をスキップし、収集だけ行う(警告ログ) |

`.env.example` に `DISCORD_WEBHOOK_URL_PTS=` を追記する。`.env` と secrets は**絶対にコミットしない**。

## 8. エラーハンドリングと異常系

| 事象 | 挙動 |
|---|---|
| TDnet APIがHTTPエラー | 例外を送出しジョブを失敗させる(Actionsの失敗通知で気づける) |
| TDnetのJSONが壊れている | 同上 |
| 株探がHTTPエラー(403/503) | 例外を送出しジョブを失敗させる。**リトライループを書かない**(相手に負荷をかけない) |
| 株探のHTMLは200だが表が見つからない | `parse_ranking_html` が `[]` を返す。IRが存在するのにランキングが0件だった場合のみ、標準出力に `[warn] PTSランキングのパースに失敗した可能性(表が見つからない)` を出し、**exit 1 で終了する**(構造変更を放置すると静かに通知が止まるため) |
| 表はあるが行のセル数が13でない | その行だけスキップし `[warn]` を出す。全行スキップなら上と同じ扱い |
| Discord送信が失敗 | 既存 `send_discord` と同じく `RuntimeError` を送出。**JSONLへの追記は送信成功後に行う**(失敗した通知を「送信済み」にしない) |
| 引け後IRが0件 | 正常終了(exit 0)。株探へはアクセスしない |
| 通知候補が0件 | 正常終了。スナップショットだけ追記される |

ログは標準出力に日本語で出す(既存 `run.py` と同じ流儀)。1回の実行で最低限これだけ出すこと:

```
2026-08-27: 適時開示 167件 / 15:30以降 122件 / 除外後 116件 / 対象銘柄 98件
PTSランキング: 2ページ取得、上昇率1.0%以上 23件
突合: 5件(うち通知条件を満たす 3件)
通知: 3件(既通知でスキップ 0件)
```

## 9. 検証用データの保全(バックテストは将来フェーズ)

ユーザーの用途は「材料の早期把握」であり、売買ルールは裁量である。よって
**本フェーズではバックテストを実装しない**。ただし、後から「この通知は翌日役に立ったか」を
検証できるように、**判断材料になるデータだけは最初から貯めておく**。

そのための設計上の仕掛けが2つある:

1. **通知閾値(+3.0%)より低い +1.0% まで収集して `data/pts_snapshots/` に保存する。**
   通知したものだけを保存すると「閾値を下げたらどうだったか」が永遠に検証できない
2. **スナップショットは重複排除せず、実行のたびに追記する。**
   17:45 / 20:00 / 23:00 の3時点が残るので、「夜が更けるにつれてPTSがどう動いたか」
   (初動で買われて失速したか、じり高だったか)が後から追える

将来バックテストを実装するときは、J-Quantsの日足(`AdjO` / `AdjH` / `AdjC`)を
`~/Claude_Project/trade_dashboard` 側の実装を参考に取得し、スナップショットの各行に
「翌営業日の始値ギャップ率」「翌営業日の寄り→引けリターン」を付与するところから始める。
実装時は `jquants-api` スキルを必ず読むこと(v2 API・5桁コード・調整済み株価の扱いがある)。

**最低3か月ぶん(通知が概ね100件以上)貯まるまでは検証しない。**それ以前の集計は
サンプル数不足で意味のある結論が出ず、閾値をいじる根拠にもならない。

## 10. 実装フェーズ計画

### フェーズ1: データ疎通(いちばん不確実な部分を最初に潰す)

作るもの: `collector/sources/tdnet.py`、`collector/sources/pts.py`、
`tests/fixtures/pts_night_20260827.html`、`requirements.txt` への `beautifulsoup4` 追加

固定HTMLの取得(**PTSナイトタイムセッション中=平日16:30〜23:59に実行すること**。時間外だと空になる):

```bash
cd ~/Claude_Project/disclosure_radar && mkdir -p tests/fixtures && curl -s \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "https://kabutan.jp/warning/pts_night_price_increase?market=0&capitalization=-1&dispmode=normal&stc=&stm=0&page=1" \
  -o tests/fixtures/pts_night_20260827.html && wc -c tests/fixtures/pts_night_20260827.html
```

動作確認:

```bash
cd ~/Claude_Project/disclosure_radar && .venv/bin/python -c "
from collector.sources import tdnet, pts
import datetime
irs = tdnet.fetch_after_close(datetime.date(2026, 8, 27))
print('引け後IRのある銘柄数:', len(irs))
print('9832のIR件数:', len(irs.get('9832', [])))
rows = pts.parse_ranking_html(open('tests/fixtures/pts_night_20260827.html', encoding='utf-8').read())
print('ランキング行数:', len(rows))
print('先頭行:', rows[0])
"
```

完了条件:

- 「引け後IRのある銘柄数」が **98**、「9832のIR件数」が **2**(TDnetは過去日を再取得できるので、いつ実行してもこの数になる)
- 「ランキング行数」が **15**(1ページ15件)
- **2026-08-27に取得した固定HTMLを使う場合**、「先頭行」が
  `{'code': '7743', 'name': 'シード', 'market': '東Ｓ', 'close': 635.0, 'pts_price': 735.0, 'rate': 15.75, 'volume': 1100, 'turnover': 808500}` と一致すること
- **別の日に固定HTMLを取り直した場合**は上の値とは一致しない。その場合は
  「15行取れている」「`code`が4文字」「`rate`が降順」「`turnover == int(pts_price * volume)`」の4点を確認すればよい
  (ファイル名も取得日に合わせて `pts_night_YYYYMMDD.html` に変える)

### フェーズ2: 突合とスナップショット(通知なし)

作るもの: `config/pts.yml`、`collector/pts_run.py`、`collector/normalize.py` の `items_dir` 対応

```bash
cd ~/Claude_Project/disclosure_radar && .venv/bin/python -m collector.pts_run --no-notify
```

完了条件: §8のサマリーログが出て、`data/pts_snapshots/2026-08.jsonl` に行が追記される。
`--no-notify` ではDiscordに何も飛ばないこと。

### フェーズ3: Discord通知と1日1回制御

作るもの: `collector/notify.py` の `ir_pts` カテゴリと `send_pts_alerts`、
`.env` / `.env.example` の `DISCORD_WEBHOOK_URL_PTS`、`tests/test_pts.py`

```bash
cd ~/Claude_Project/disclosure_radar && .venv/bin/python -m collector.pts_run && \
  .venv/bin/python -m collector.pts_run   # 2回目
```

完了条件: 1回目でDiscordに通知が届き、**2回目は「既通知でスキップ」となり何も届かない**こと。
`.venv/bin/python -m pytest tests/ -q` が全て通ること(既存テストも壊れていないこと)。

### フェーズ4: GitHub Actions

作るもの: `.github/workflows/pts.yml`、リポジトリsecret `DISCORD_WEBHOOK_URL_PTS`

```bash
cd ~/Claude_Project/disclosure_radar && gh workflow run pts.yml && sleep 60 && gh run list --workflow=pts.yml --limit 3
```

完了条件: `workflow_dispatch` での手動実行が成功し、`data/pts_snapshots/` の更新がmasterにcommitされること。
**pushはSSHリモート経由**(ghのOAuthトークンにworkflowスコープが無く、ワークフローファイルのpushはHTTPSだと拒否される)。

## 11. 受け入れ基準

- [ ] 平日の夜、引け後IRが出てPTSで+3.0%以上・売買代金100万円以上の銘柄があればDiscordに通知が届く
- [ ] 同じ銘柄が同じ日に2回以上通知されない
- [ ] 同じ銘柄に複数のIRがある場合、1通の中に最大3件まとめて表示される
- [ ] 引け後IRが0件の日は株探にアクセスせず、正常終了する
- [ ] `data/pts_snapshots/YYYY-MM.jsonl` に、通知しなかった +1.0%以上の銘柄も記録されている
- [ ] 株探へのアクセスがページ間3秒以上あいている
- [ ] 既存のEDINET収集(`python -m collector.run`)と大量保有通知の挙動が**一切変わっていない**
- [ ] 既存テストを含め `pytest` が全て通る
- [ ] コードがPython 3.7で動く(`.venv/bin/python` = 3.7.3 で実行できる)
- [ ] `.env` / Webhook URL / secrets がコミットに含まれていない

## 12. 落とし穴・やってはいけないこと

1. **銘柄名のセルを `<td>` だと思って `find_all("td")` でパースする** → 列が1つずれて全部壊れる。
   銘柄名は `<th scope="row">` である。必ず `find_all(["td", "th"])` で13セルとして扱う
2. **列名(`通常取引27日終値`)を文字列で探す** → 「27」は日付なので毎日変わる。**列インデックスで取る**
3. **User-Agentを付けずに株探へアクセスする** → 403が返る。§5.3のUAをそのまま使う
4. **株探に高頻度アクセスする / リトライループを書く** → `robots.txt` の `Crawl-delay: 3` を守り、
   ページ間に3秒sleepを入れる。失敗時は素直に落とす
5. **`pts_day_price_increase`(デイタイム)を使う** → 正しいのは `pts_night_price_increase`。
   引け後IRの反応を見るのはナイトタイムセッションである
6. **TDnetの5桁コードのまま突合する** → 株探は4桁。`company_code[:4]` に揃える。
   `"154A0"` → `"154A"` のように英字入りコードも同じ規則で通る
7. **前回実行からの差分だけを見る実装にする** → GitHub Actionsのcronは大幅に間引かれる。
   毎回「その日の全IR」と「ランキング全体」を見直し、通知済みidで重複排除する設計にすること
8. **Discord送信の前にJSONLへ追記する** → 送信に失敗した銘柄が「通知済み」になって永久に届かなくなる。
   **追記は送信成功後**
9. **Python 3.8以降の構文を使う** → このリポジトリは3.7互換が絶対条件(ウォルラス演算子 `:=`、
   f-stringの `=` 指定、`functools.cached_property` などはNG)。文字列整形は `"...".format()` を使う
10. **既存の `send_discord` / `_to_embed` / `config/rules.yml` を作り変える** → 稼働中の大量保有通知が壊れる。
    PTS通知は `send_pts_alerts` として**追加**し、閾値は `config/pts.yml` に置く
11. **`collector/run.py`(EDINET収集)に相乗りする** → 20分ごとにPTS処理が走る。別ワークフロー・別エントリポイント
12. **`git add data/` でcommitする(ptsジョブ側)** → EDINET収集の途中結果を巻き込む。
    `data/pts_alerts data/pts_snapshots` にパスを限定する
13. **スナップショットを重複排除する** → 時刻ごとの値動きが残らず、後から検証できなくなる(§9)
14. **休場日判定・祝日カレンダーを実装する** → 不要。IRが0件なら自然に0件で終わる
15. **銘柄ごとに `kabutan.jp/stock/?code=XXXX` を叩く** → 引け後IRは1日100銘柄以上ある。
    ランキング1〜2ページの取得で足りるところを100リクエストにしてはいけない
