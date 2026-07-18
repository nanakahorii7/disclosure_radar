# disclosure_radar 設計書

需給に直結する開示情報(大量保有報告書・自社株買い等)を24時間自動収集し、
タイムライン表示とDiscord通知を行う個人用システム。

- 作成日: 2026-07-10 / 最終更新: 2026-07-11
- ステータス: フェーズ1実装済み(PR #1)。実装後レビューでの決定(public化・actions/cache・保有割合パース前倒し)を反映済み

---

## 1. 目的・背景

トレード判断に使う「大口の痕跡」— 大量保有報告書(5%ルール)、変更報告書、
自社株買いなどの需給インパクトが大きい開示 — を、寝ている間も自動で収集・通知する。

当初はGoogle Alerts + RSSリーダー + IFTTT/Zapier + LINE通知の構成を検討したが、
以下の理由で自作システム中心の構成に決定した:

| 当初案の課題 | 対応 |
|---|---|
| LINE Notifyが2025年3月に終了、IFTTT→LINEの定番経路が消滅 | 通知はDiscord Webhookに変更 |
| Zapier/IFTTTの無料枠が縮小(月100タスク等)、開示件数に耐えない | 通知処理を自作(件数制限なし) |
| Google Alertsは報道ベースで遅い・取りこぼす | 一次ソース(EDINET API・TDnet)を直接取得。Google Alertsは補助ソースとして併用 |
| Feedly等は既読管理・フィルタが汎用的すぎる | 自作タイムラインUI(銘柄コード・カテゴリでフィルタ) |

## 2. 全体アーキテクチャ

収集・通知は**GitHub Actions(クラウド)**で定期実行し、Macがスリープ中でも動く。
閲覧UIは**ローカルのFastAPI**で、起動時にリポジトリをpullして最新データを表示する。

```mermaid
flowchart LR
    subgraph sources[情報ソース]
        E[EDINET API v2<br>大量保有・変更報告書]
        T[TDnet<br>自社株買い等の適時開示]
        G[Google Alerts RSS<br>ニュース補完]
    end

    subgraph gha[GitHub Actions - 15〜30分毎]
        C[collector<br>取得・正規化・重複排除]
        N[notifier<br>ルール判定]
    end

    subgraph repo[GitHubリポジトリ]
        D[(data/items/YYYY-MM.jsonl)]
    end

    subgraph mac[ローカルMac]
        V[viewer<br>FastAPI + 素のJS<br>ポート8100]
    end

    E --> C
    T --> C
    G --> C
    C -->|新着をcommit&push| D
    C --> N
    N -->|Webhook| DC[Discord通知<br>スマホにプッシュ]
    D -->|git pull| V
```

**データの真実はリポジトリにcommitされるJSONL**(いわゆるgit scraping方式)。
DBサーバー不要・履歴が全部gitに残る・MacとActionsのデータ同期がgit pullで済む。

## 3. 情報ソース

### 3.1 EDINET API v2(一次ソース・最重要)

- 書類一覧API: `https://api.edinet-fsa.go.jp/api/v2/documents.json?date=YYYY-MM-DD&type=2`
- 認証: `Subscription-Key`(EDINETアカウント登録で無料発行)。ローカルは`.env`、Actionsはリポジトリsecretに置く
- 抽出対象: **府令コード(ordinanceCode)= 060(大量保有府令)** で絞り、
  大量保有報告書・変更報告書・訂正報告書をまとめて拾う
  (実データで確認済み: docTypeCode 350、変更報告書はformCode 010002。区別はdocDescriptionの文字列で行う)
- 取得できる構造化フィールド: 提出者名(=買った大口の名前)、対象発行会社のEDINETコード、提出日時
  - 発行会社名・4桁銘柄コードは**EDINETコード一覧CSV**(金融庁配布のEdinetcode.zip)で解決する。
    一覧はローカルでは`data/`に、Actionsでは`actions/cache`にキャッシュし7日毎に更新(**リポジトリにはコミットしない**)
- **保有割合パース**(当初フェーズ4予定を前倒しで実装済み): 新着書類ごとに書類取得API
  `type=5`(XBRLのCSV版)を取り、`jplvh_cor:HoldingRatioOfShareCertificatesEtc`(今回)と
  `...PerLastReport`(前回)を抽出。共同保有は合計行(コンテキスト`FilingDateInstant`)を優先し、
  合計行が無い単独保有者の書類は保有者別行から合算する。取得は新着分のみ(既知の書類には再アクセスしない)

### 3.2 TDnet(一次ソース)

- JPX公式APIは有料のため、非公式の[やのしんTDnet WEB-API](https://webapi.yanoshin.jp/tdnet/)を使う
  - 例: `https://webapi.yanoshin.jp/webapi/tdnet/list/recent.json`(数分毎にTDnetと同期、RSS/JSON/XML対応)
- 抽出対象(表題のキーワードマッチ):
  - `自己株式の取得`(自社株買い決議)
  - `自己株式立会外買付取引`(ToSTNeT-3)
  - `公開買付`(TOB)
  - キーワードは設定ファイル`config/sources.yml`で追加・変更可能
- **リスク**: 非公式APIのため停止リスクあり。アダプタ層を挟み、止まったら
  TDnet閲覧サービスの直接スクレイピングに差し替えられる構造にする(§10)

### 3.3 Google Alerts RSS(補助ソース)

- ユーザーがGoogle Alertsで「大量保有報告書」「自社株買い」「5%ルール」等を登録し、
  配信先を「RSSフィード」に設定 → 発行されたフィードURLを`config/sources.yml`に貼る
- collectorが他ソースと同じ形式に正規化して取り込む(feedparserを使用)
- 位置づけ: EDINET/TDnetに載らない周辺情報(観測記事・思惑報道)の補完。一次ソースより遅い前提

## 4. データ設計

### 4.1 アイテム共通スキーマ

全ソースを以下の1レコード形式に正規化する:

```json
{
  "id": "edinet:S100XXXX",
  "source": "edinet",
  "category": "large_holding",
  "title": "大量保有報告書(株式会社◯◯)",
  "url": "https://disclosure2.edinet-fsa.go.jp/...",
  "code": "7203",
  "company": "トヨタ自動車",
  "filer": "◯◯アセットマネジメント",
  "ratio": 6.26,
  "prev_ratio": 5.02,
  "published_at": "2026-07-10T15:32:00+09:00",
  "collected_at": "2026-07-10T15:45:12+09:00",
  "tags": ["大量保有"],
  "raw": {}
}
```

- `id`: ソース + ソース内一意キー(EDINETはdocID、TDnetは開示ID、RSSはURLのハッシュ)。**重複排除のキー**
- `category`: `large_holding` / `change_report` / `buyback` / `tob` / `news`
- `code` / `filer`: EDINET・TDnetでは構造化データから取得。newsはnull可
- `ratio` / `prev_ratio`: 株券等保有割合%(EDINETの大量保有系のみ。他ソースはnull)
- `raw`: ソース固有の生フィールドを保持(後からのパース強化用)

### 4.2 保存形式

- `data/items/YYYY-MM.jsonl` — 月ごとに1ファイル、1行1アイテム、追記のみ
- collectorは「既存JSONLに無いidだけ」を追記 → これが重複排除と「新着」判定を兼ねる
- viewerは起動時にJSONLを読み込みローカルSQLiteに展開(既読フラグ等のローカル状態もSQLite側に持つ)

## 5. 収集パイプライン(GitHub Actions)

### 5.1 ワークフロー

`.github/workflows/collect.yml`:

1. checkout(データブランチ=masterごとpull)
2. Python環境セットアップ + 依存インストール
3. `python -m collector.run` — 全ソース取得 → 正規化 → 新着抽出 → JSONL追記 → 通知判定・Discord送信
4. 変更があれば `data/` をcommit & push(コミットメッセージ: `収集: 新着N件 (YYYY-MM-DD HH:MM)`)
5. `concurrency`設定で多重実行を防止

### 5.2 実行スケジュール

開示の実態に合わせてメリハリをつける(大量保有報告書は平日15時以降に集中提出):

| 時間帯(JST) | 間隔 | ねらい |
|---|---|---|
| 平日 8:00〜19:00 | 20分毎 | EDINET・TDnetの開示時間帯 |
| 上記以外(夜間・休日) | 60分毎 | Google Alerts経由の海外時間ニュース拾い |

- 実行回数は月約1,180回で、分単位切り上げ課金だとprivateの無料枠(月2,000分)を超えるおそれがあるため、
  **リポジトリはpublicで運用する(Actions枠無制限。扱うのは公開開示情報のみ)**。2026-07-11にpublic化済み
- **GitHub Actionsのcronは数分〜数十分遅延することがある**。本システムの速報性は
  「最大30分程度の遅れは許容」という前提で設計する(それでも翌朝発見よりはるかに速い)

### 5.3 ローカルフォールバック

collectorはMacでも `python -m collector.run` でそのまま動くようにする
(Actions障害時・過去日の一括取り込み・デバッグ用)。このため**コードは全てPython 3.7互換**で書く
(ActionsのPythonは3.11等でよいが、構文は3.7に合わせる。ウォルラス演算子等は使わない)。

## 6. 通知設計(Discord)

- 通知先: 自分専用Discordサーバーのチャンネル + **Webhook URL**(Actionsのsecret `DISCORD_WEBHOOK_URL`)
- スマホのDiscordアプリでプッシュ受信 → 「寝ている間もLINEのように通知」の要件を満たす

### 6.1 通知ルール(`config/rules.yml`)

収集は全件(変更報告書・訂正含む)行いタイムラインに残す。**ここで絞るのは通知だけ**。
大量保有府令は1日40〜70件出るため全件通知はノイズになる。既定は
「新規の5%超え」と「大口の売り抜け(5%割れ)」の2ルール:

```yaml
rules:
  - name: 新規の大量保有報告書(5%超え)のみ通知
    match:
      category: [large_holding]
      title_not_contains: [訂正]
  - name: 大口の売り抜け(5%割れ)
    label: 売り抜け
    emoji: "📉"
    color: 0xD32F2F
    match:
      category: [change_report]
      title_not_contains: [訂正]
      prev_ratio_min: 5
      ratio_below: 5
```

- matchの語彙: `category` / `title_contains` / `title_not_contains` /
  `prev_ratio_min`(前回割合が値以上) / `ratio_below`(今回割合が値未満)。すべてAND。
  保有割合が取れていない書類は数値条件を満たさない(=通知しない)フェイルセーフ
- ルールに `label` / `emoji` / `color` を書くと通知見出しをカテゴリ既定から上書きできる
  (売り抜けルールは📉・赤 0xD32F2F)
- 実測: 新規5%超えは1日40件超→数件程度(2026-07-08実データ: 43件中6件)、
  売り抜けは約1週間の実データで20件≒1日3〜5件(設計書: docs/superpowers/specs/2026-07-18-exit-notify-rule-design.md)
- ルールは宣言的に書き、collectorが新着アイテムに対して評価する
- 通知フォーマット(Discord embed): カテゴリ絵文字 + 銘柄コード・社名 + 表題 + 提出者 + **保有割合** + 原文リンク
  - 例: `🐋 大量保有報告書 | [2201] 森永製菓 | 提出者: ◯◯アセット | 保有割合: 5.02% (新規) | 10:25`
- 大量新着時(初回実行・障害復旧後)は最大10件+「他N件」に丸めて通知爆発を防ぐ
- 通知済み管理は不要(「JSONLに無かった=新着」の1回だけ通知する仕組みのため)

## 7. 閲覧UI(ローカル)

- 構成: FastAPI + 素のJS(trade_dashboardと同じ流儀)、**ポート8100**(8000はtrade_dashboardが使用中)
- 起動: `.venv/bin/python -m uvicorn viewer.main:app --port 8100`
- 起動時と「更新」ボタンで `git pull` → JSONL再読み込み

### 7.1 画面構成(1画面)

- **タイムライン**: 新着順の縦一列(ニュースアプリ風)。カテゴリ別の色バッジ(🐋大量保有 / 🔄変更 / 💰自社株買い / 📰ニュース)
- **フィルタバー**: カテゴリ / 銘柄コード / キーワード / 期間
- **既読管理**: クリックで既読(グレーアウト)。未読件数をヘッダーに表示。既読状態はローカルSQLiteのみ(commitしない)
- 各行から原文(EDINET/TDnet/記事)へワンクリックで飛べる

### 7.2 API

| エンドポイント | 内容 |
|---|---|
| `GET /api/items?category=&code=&q=&since=&until=` | タイムライン取得(ページング付き) |
| `POST /api/items/{id}/read` | 既読化 |
| `POST /api/refresh` | git pull + 再読み込み |

## 8. リポジトリ構成

```
disclosure_radar/
├── CLAUDE.md               # 開発上の約束事(trade_dashboardに準ずる)
├── docs/DESIGN.md          # 本書
├── config/
│   ├── sources.yml         # Google AlertsフィードURL・TDnetキーワード等
│   └── rules.yml           # 通知ルール
├── collector/
│   ├── run.py              # エントリポイント(取得→正規化→追記→通知)
│   ├── sources/
│   │   ├── edinet.py       # EDINET API v2アダプタ(書類一覧+保有割合XBRLパース)
│   │   ├── edinet_codes.py # EDINETコード一覧(発行会社名・銘柄コード解決、actions/cacheでキャッシュ)
│   │   ├── tdnet.py        # やのしんWEB-APIアダプタ(差し替え可能な構造)
│   │   └── google_alerts.py
│   ├── normalize.py        # 共通スキーマへの正規化・重複排除
│   └── notify.py           # ルール評価 + Discord Webhook送信
├── viewer/
│   ├── main.py             # FastAPIアプリ
│   └── static/             # index.html / app.js / style.css
├── data/items/             # YYYY-MM.jsonl(collectorがcommit)
│                           # ※ data/edinet_codes.* はコミットせずactions/cache(gitignore済み)
├── .github/workflows/collect.yml
├── .env.example            # EDINET_API_KEY / DISCORD_WEBHOOK_URL
└── requirements.txt        # requests, feedparser, pyyaml, fastapi, uvicorn
```

## 9. 秘匿情報の扱い

| 情報 | ローカル | GitHub Actions |
|---|---|---|
| EDINET APIキー | `.env`(gitignore) | リポジトリsecret `EDINET_API_KEY` |
| Discord Webhook URL | `.env`(gitignore) | リポジトリsecret `DISCORD_WEBHOOK_URL` |

- 扱うデータ自体は全て公開開示情報のため、data/のcommitに機密性の問題はない
- **リポジトリはpublic**だがsecretは露出しない(ワークフローログへの出力に注意)
- **注意(フェーズ3)**: Google AlertsのフィードURLは「URLを知っている人だけ読める」タイプなので、
  publicリポジトリの `config/sources.yml` には書かず、secret化するか別の受け渡し方法にすること

## 10. 制約・リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| やのしんAPI(非公式)の停止 | TDnet系が取れなくなる | アダプタ層で分離。停止時はTDnet閲覧サービスの直接スクレイピングに実装差し替え。取得失敗が続いたらDiscordに運用アラートを送る |
| GitHub Actions cronの遅延 | 通知が最大30分程度遅れる | 許容する設計(§5.2)。将来より高頻度が欲しくなったらMac常時稼働 or 有料の軽量VMに移す |
| ~~Actions無料枠(private月2,000分)超過~~ | — | **public化で解消済み**(2026-07-11)。publicはスケジュール実行が60日間リポジトリ更新なしで停止する仕様だが、収集が毎日commitするため実質問題なし |
| 収集が2日以上止まると取りこぼしが恒久化 | 停止期間の書類を拾えない | `--days-back 7` の穴埋めジョブを週1で追加する(未実装・TODO) |
| ワークフロー失敗に気づけない | 収集停止の放置 | `if: failure()` でDiscordに失敗通知を送るステップを追加する(未実装・TODO) |
| Google Alertsのフィード仕様変更・取りこぼし | ニュース補完の欠落 | 補助ソースの位置づけなので致命的でない。一次ソースはEDINET/TDnet |
| EDINET APIのメンテナンス・仕様改訂 | 収集の一時停止 | 取得失敗の連続をDiscordにアラート。JSONLは追記式なので復旧後に過去日の穴埋め再取得が可能 |
| ローカルPython 3.7.3制約 | 新しい構文・ライブラリが使えない | 全コード3.7互換で統一(§5.3)。依存は requests / feedparser / pyyaml / fastapi の枯れた版に固定 |

## 11. 実装フェーズ

| フェーズ | 内容 | 完了条件 |
|---|---|---|
| **1. コア** ✅実装済み | EDINET収集 + 保有割合XBRLパース(フェーズ4から前倒し) + JSONL保存 + Discord通知をActionsで稼働 | 平日夕方、「[コード] 社名 / 保有割合 X% (新規)」の通知がスマホに届く |
| **2. 閲覧UI** | FastAPIタイムライン(フィルタ・既読) | 朝起きて夜間の新着を一覧で振り返れる |
| **3. ソース拡充** | TDnet(自社株買い・TOB) + Google Alerts RSS取り込み | 通知ルールがカテゴリ別に効いている |
| **4. 深掘り** | trade_dashboardウォッチリストとの連動(ウォッチ銘柄だけ強調通知)、通知ルールの数値条件(買い増しのみ等)、共同保有者の内訳表示 | ウォッチ銘柄の大口の動きを見逃さない |

フェーズ1が最小価値(寝ている間の自動収集+通知)。ここまで最短で作って運用を始め、
使い勝手を見てから2以降を進める。

## 12. 未決事項

- Discordのチャンネル分割(カテゴリ別に分けるか、1チャンネルに流すか)— まず1チャンネルで開始
- フェーズ4のウォッチリスト連動方式(trade_dashboardのDB参照 or ウォッチリストのエクスポート)
- 穴埋めジョブと失敗通知の追加(§10のTODO)

### 解決済み

- ~~変更報告書・訂正報告書の正確なdocTypeCode/formCode~~ → 実データで確認(docTypeCode 350、変更報告書はformCode 010002。区別はdocDescriptionで行う)
- ~~private/publicの判断~~ → public化(2026-07-11、Actions枠対策)
