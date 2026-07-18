# 大口の売り抜け通知(5%割れ)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 変更報告書で保有割合が前回5%以上→今回5%未満になった「大口の売り抜け」をDiscordに📉通知する。

**Architecture:** 通知ルール(config/rules.yml)のmatch語彙に数値条件2つ(`prev_ratio_min`/`ratio_below`)を追加し、判定関数を「マッチしたルールを返す」形に変更。ルール側の`label`/`emoji`/`color`でembed見出しを上書きする。collector本体・データスキーマ・Actionsは変更しない。

**Tech Stack:** Python 3.7互換 / requests / PyYAML / pytest(開発時のみ)

## Global Constraints

- コードは**Python 3.7互換構文**(ウォルラス演算子・f-stringの`=`指定は禁止)。ローカル`.venv`は3.7.3
- 実行時依存は追加しない。pytestは開発専用で`requirements-dev.txt`に分離(`requirements.txt`には入れない)
- コミットメッセージは日本語・1行・体言止め、末尾に`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- masterへ直接コミットしない。作業は`feature/exit-notify-rule`ブランチ(作成済み・設計書コミット済み)、最後にPR
- 既存の「新規の大量保有報告書」通知の挙動を変えない
- 仕様書: `docs/superpowers/specs/2026-07-18-exit-notify-rule-design.md`

---

### Task 1: notify.py の判定関数拡張(数値条件 + マッチしたルールを返す)

**Files:**
- Create: `tests/test_notify.py`
- Create: `requirements-dev.txt`
- Modify: `collector/notify.py`(`match_rules`を`find_matching_rule`+`_match_one`に置き換え)
- Modify: `.gitignore`(`.pytest_cache/`追加)

**Interfaces:**
- Consumes: 既存の`notify.load_rules()`、アイテムdict(`category`/`title`/`ratio`/`prev_ratio`キー)
- Produces: `find_matching_rule(item, rules) -> dict or None`(最初にマッチしたルールを返す。Task 2/3が使用)。旧`match_rules`は削除

- [ ] **Step 1: pytestを開発用依存として導入**

`requirements-dev.txt` を作成:

```
pytest>=7,<8
```

実行: `cd ~/Claude_Project/disclosure_radar && .venv/bin/pip install --quiet -r requirements-dev.txt && .venv/bin/python -m pytest --version`
期待: `pytest 7.x` が表示される

`.gitignore` に1行追加:

```
.pytest_cache/
```

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_notify.py` を作成(tests/に`__init__.py`は不要):

```python
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
```

- [ ] **Step 3: テストが失敗することを確認**

実行: `.venv/bin/python -m pytest tests/test_notify.py -v`
期待: 全テストがFAIL(`AttributeError: module 'collector.notify' has no attribute 'find_matching_rule'`)

- [ ] **Step 4: notify.py を実装**

`collector/notify.py` の `match_rules` 関数(29〜44行付近)を丸ごと以下に置き換える:

```python
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
```

- [ ] **Step 5: テストが通ることを確認**

実行: `.venv/bin/python -m pytest tests/test_notify.py -v`
期待: 7件全部PASS

- [ ] **Step 6: コミット**

```bash
git add tests/test_notify.py requirements-dev.txt .gitignore collector/notify.py
git commit -m "通知ルールに保有割合の数値条件を追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

※ この時点で`run.py`は旧`match_rules`を呼んでおり一時的に壊れるが、Task 2で追従する(Actionsはmasterで動いているため影響なし)

---

### Task 2: embed表示の上書き(label/emoji/color)と run.py の追従

**Files:**
- Modify: `collector/notify.py`(`send_discord`/`_to_embed`)
- Modify: `collector/run.py`(判定まわり)
- Test: `tests/test_notify.py`(追記)

**Interfaces:**
- Consumes: Task 1の`find_matching_rule(item, rules) -> dict or None`
- Produces: `send_discord(matched, webhook_url) -> int`(`matched`は`(item, rule)`タプルのリスト)、`_to_embed(item, rule=None) -> dict`

- [ ] **Step 1: 失敗するテストを追記**

`tests/test_notify.py` の末尾に追加:

```python
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
```

実行: `.venv/bin/python -m pytest tests/test_notify.py -v -k embed`
期待: 2件FAIL(`_to_embed() takes 1 positional argument but 2 were given`)

- [ ] **Step 2: notify.py の送信・embed生成を実装**

`send_discord` と `_to_embed` を以下に置き換える(docstringと引数が変わる):

```python
def send_discord(matched, webhook_url):
    """(アイテム, マッチしたルール)のリストをDiscordに通知する。上限超過分は件数だけ知らせる。"""
    if not matched:
        return 0
    if not webhook_url:
        print("[warn] DISCORD_WEBHOOK_URL未設定のため通知をスキップ({}件)".format(len(matched)))
        return 0

    embeds = [_to_embed(item, rule) for item, rule in matched[:MAX_EMBEDS]]
    payload = {"embeds": embeds}
    rest = len(matched) - MAX_EMBEDS
    if rest > 0:
        payload["content"] = "ほか{}件の新着があります(タイムラインで確認)".format(rest)

    res = requests.post(webhook_url, json=payload, timeout=30)
    if res.status_code not in (200, 204):
        raise RuntimeError("Discord通知に失敗: HTTP {} {}".format(res.status_code, res.text[:200]))
    time.sleep(1)  # 連続実行時のレート制限対策
    return len(matched)
```

`_to_embed` は先頭部分だけ次のように変更(description組み立て以降は既存のまま):

```python
def _to_embed(item, rule=None):
    emoji, label, color = _CATEGORY_LABEL.get(item.get("category"), ("\U0001F4C4", "開示", 0x757575))
    if rule:
        emoji = rule.get("emoji", emoji)
        label = rule.get("label", label)
        color = rule.get("color", color)
```

- [ ] **Step 3: run.py を追従させる**

`collector/run.py` の通知判定部分(`rules = notify.load_rules()`から`print("通知: ...")`まで)を以下に置き換える:

```python
    rules = notify.load_rules()
    matched = []
    for item in new_items:
        rule = notify.find_matching_rule(item, rules)
        if rule is not None:
            matched.append((item, rule))
    if args.no_notify:
        print("通知対象: {}件(--no-notifyのためスキップ)".format(len(matched)))
    else:
        sent = notify.send_discord(matched, os.environ.get("DISCORD_WEBHOOK_URL"))
        print("通知: {}件".format(sent))
```

- [ ] **Step 4: 全テストと構文チェック**

実行: `.venv/bin/python -m pytest tests/ -v && .venv/bin/python -m py_compile collector/run.py collector/notify.py`
期待: 9件全部PASS、py_compileエラーなし

- [ ] **Step 5: コミット**

```bash
git add tests/test_notify.py collector/notify.py collector/run.py
git commit -m "通知embedのルール別表示上書きとrun.pyの追従

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: ルール定義・ドキュメント更新・実データ検証

**Files:**
- Modify: `config/rules.yml`(売り抜けルール追加)
- Modify: `docs/DESIGN.md`(§6.1の語彙説明)

**Interfaces:**
- Consumes: Task 1の数値条件語彙、Task 2の表示上書き
- Produces: 本番で使われるルール定義(これがデプロイ物)

- [ ] **Step 1: rules.yml にルールを追加**

`config/rules.yml` を以下の内容に置き換える:

```yaml
# Discord通知ルール。新着アイテムがいずれかのルールにマッチしたら通知する。
# ※ 収集自体は全件(変更報告書・訂正含む)行われ、タイムラインには全部残る。ここで絞るのは通知だけ。
#
# match の条件(すべてAND。ratio/prev_ratioが取れていない書類は数値条件を満たさない=通知しない):
#   category:           このリストのいずれかに一致(large_holding / change_report / buyback / tob / news)
#   title_contains:     タイトルにこのリストのいずれかの文字列を含む(省略可)
#   title_not_contains: タイトルにこのリストの文字列を含む場合は除外(省略可)
#   prev_ratio_min:     前回の株券等保有割合がこの値以上(省略可)
#   ratio_below:        今回の株券等保有割合がこの値未満(省略可)
# ルールの表示設定(省略時はカテゴリ既定): label / emoji / color
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

- [ ] **Step 2: 実データでドライラン**

実行:

```bash
.venv/bin/python -c "
# -*- coding: utf-8 -*-
import json
from collector import notify
rules = notify.load_rules()
count = 0
for line in open('data/items/2026-07.jsonl', encoding='utf-8'):
    it = json.loads(line)
    rule = notify.find_matching_rule(it, rules)
    if rule is not None and rule.get('label') == u'売り抜け':
        count += 1
        print(u'{} {} {} {}% -> {}%'.format(it.get('code'), it.get('company'),
              it.get('filer'), it.get('prev_ratio'), it.get('ratio')))
print(u'売り抜け通知対象: {}件'.format(count))
"
```

期待: 設計時調査の23件から訂正分が除かれた件数(15〜23件の範囲)が列挙され、
ディー・エヌ・エー[2432] 5.06%→3.76% が含まれる

- [ ] **Step 3: DESIGN.md §6.1 を更新**

`docs/DESIGN.md` §6.1の「変更報告書(買い増し・売り抜けの動き)も通知したくなったら…数値条件の追加実装を検討する」の行を削除し、rules.yml例を上のStep 1と同じ2ルール構成に差し替え。さらに例の下に以下を追記:

```markdown
- matchの語彙: `category` / `title_contains` / `title_not_contains` /
  `prev_ratio_min`(前回割合が値以上) / `ratio_below`(今回割合が値未満)。すべてAND。
  保有割合が取れていない書類は数値条件を満たさない(=通知しない)フェイルセーフ
- ルールに `label` / `emoji` / `color` を書くと通知見出しをカテゴリ既定から上書きできる
  (売り抜けルールは📉・赤 0xD32F2F)
```

- [ ] **Step 4: 実際にDiscordへテスト通知を1件送る**

実行:

```bash
.venv/bin/python -c "
# -*- coding: utf-8 -*-
import os
from collector import notify
from collector.run import load_dotenv
load_dotenv()
rules = notify.load_rules()
item = {
    'id': 'test:exit-rule-check', 'source': 'edinet', 'category': 'change_report',
    'title': u'変更報告書(売り抜け通知テスト)', 'url': 'https://github.com/nanakahorii7/disclosure_radar',
    'code': '0000', 'company': u'これはテスト通知です', 'filer': u'disclosure_radar',
    'ratio': 3.76, 'prev_ratio': 5.06, 'published_at': '2026-07-18T15:00:00+09:00',
}
rule = notify.find_matching_rule(item, rules)
assert rule is not None and rule.get('label') == u'売り抜け', rule
print('送信:', notify.send_discord([(item, rule)], os.environ.get('DISCORD_WEBHOOK_URL')), '件')
"
```

期待: `送信: 1 件`。Discord上で📉「売り抜け」・赤色・`保有割合: 3.76% (前回 5.06%)` を目視確認

- [ ] **Step 5: コミット**

```bash
git add config/rules.yml docs/DESIGN.md
git commit -m "大口の売り抜け(5%割れ)通知ルールを追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 仕上げ(全体確認とPR作成)

**Files:**
- なし(確認とPRのみ)

**Interfaces:**
- Consumes: Task 1〜3の全成果物
- Produces: レビュー可能なPR

- [ ] **Step 1: 全テスト+通常実行の最終確認**

実行: `.venv/bin/python -m pytest tests/ -v && .venv/bin/python -m collector.run --no-notify`
期待: テスト9件PASS、collectorが正常終了(通知対象件数が表示される。実行日によっては新規0件で通知対象0件も正常)

- [ ] **Step 2: push と PR作成**

```bash
git push -u origin feature/exit-notify-rule
gh pr create --title "大口の売り抜け(5%割れ)通知を追加" --body "## 何を変えたか
- 通知ルールのmatchに数値条件 prev_ratio_min / ratio_below を追加
- ルールのlabel/emoji/colorで通知見出しを上書きできるようにした
- 変更報告書で前回5%以上→今回5%未満を📉「売り抜け」として通知するルールを追加
- pytestを開発用依存として導入(tests/test_notify.py 9件)
- 設計書: docs/superpowers/specs/2026-07-18-exit-notify-rule-design.md

## 動作確認
- pytest 9件PASS(境界値5.0%・None・訂正除外・既存ルール非干渉・embed上書き)
- 実データ291件へのドライランで売り抜け対象を確認(DeNA 5.06%→3.76%等)
- Discordへ実テスト通知を送り📉・赤色・割合表示を目視確認
- Python 3.7で構文チェックOK

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

期待: PR URLが表示される。**マージはユーザーに委ねる**
