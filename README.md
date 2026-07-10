# disclosure_radar

需給に直結する開示情報(大量保有報告書・変更報告書・自社株買い等)を24時間自動収集し、
タイムライン表示とDiscord通知を行う個人用システム。

- 収集・通知: GitHub Actionsで定期実行(EDINET API v2 / TDnet / Google Alerts RSS)
- データ: `data/items/YYYY-MM.jsonl` にcommit(git scraping方式)
- 閲覧: ローカルのFastAPIタイムラインUI(ポート8100)

詳細は [docs/DESIGN.md](docs/DESIGN.md) を参照。
