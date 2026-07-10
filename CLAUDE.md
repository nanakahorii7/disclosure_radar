# disclosure_radar

需給系開示(大量保有報告書・自社株買い等)の自動収集+Discord通知。設計は `docs/DESIGN.md`。
収集・通知はGitHub Actions、閲覧UIはローカル(ポート8100)。

## 環境の制約(必ず守る)

- **コードは全てPython 3.7互換で書く**(ローカルは`.venv/bin/python` = 3.7.3)。
  ウォルラス演算子(`:=`)、f-stringの`=`指定などはNG。GitHub Actions側は3.11で実行される
- **`brew install` は使わない**。ツールが必要なら公式バイナリの直接配置を提案する
- `.env` に `EDINET_API_KEY` と `DISCORD_WEBHOOK_URL`。コミットに含めない
  (Actions側は同名のリポジトリsecret)

## プロジェクトの約束事

- コミット・PRは `jp-pr-workflow` スキルの運用(featureブランチ + 日本語コミット)。デフォルトブランチは `master`
- `data/items/*.jsonl` はGitHub Actionsがcommitする。**ローカル作業の前に必ず `git pull`**
- 収集のローカル実行: `.venv/bin/python -m collector.run`(手動確認・過去日の穴埋め用)
- 通知を出したくないテストは `--no-notify` を付ける
- TDnet・Google Alerts取り込み(フェーズ3)、閲覧UI(フェーズ2)は未実装。フェーズ計画はDESIGN.md §11
