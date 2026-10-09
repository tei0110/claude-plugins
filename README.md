# claude-plugins

個人用のClaudeプラグインマーケットプレイス。

## 収録プラグイン

### `my`

セッションの引き継ぎ資料の作成・読み込みと、検証が通るまで回す自己検証ループのスキル集。

| スキル | 呼び出し | 用途 |
| --- | --- | --- |
| `handover` | `/my:handover` | 現在のセッションを引き継ぎ資料にまとめて書き出す |
| `read-handover` | `/my:read-handover` | 前回の引き継ぎ資料を探して読み込む |
| `verify-loop` | `/my:verify-loop` | 検証（lint・テスト・E2E）が通るまで Claude を終了させない自己検証ループの設定・ON/OFF |

引き継ぎ資料はメイン worktree の `claudedocs/handover/` に `YYYYMMDD_HHMMSS_` 始まりで保存し、`latest.md` / `latest_wt-<NAME>.md` のシンボリックリンクが最新を指す。

`handover` / `read-handover` は `disable-model-invocation: true` を指定しているため、明示的に呼んだときだけ動く。

### verify-loop

プラグインの hook（`hooks/hooks.json`）として Stop / PostToolUse に登録される。
プロジェクトに `.claude/verify-loop.config.json` があり、`/my:verify-loop on` したセッションでだけ検証が走る（それ以外は何もしない）。

```
/my:verify-loop init      # プロジェクトの正規コマンドを読んで設定ファイルを作る
/my:verify-loop on        # 対象テストを推定して有効化（--set KEY=VALUE で明示も可）
/my:verify-loop status
/my:verify-loop off
```

設定例は `plugins/my/skills/verify-loop/examples/`（Laravel+Docker / Flutter / Node）。実行には `python3` が必要。

## 構成

```
claude-plugins/
├── .claude-plugin/
│   └── marketplace.json
└── plugins/
    └── my/
        ├── .claude-plugin/
        │   └── plugin.json
        ├── hooks/hooks.json
        └── skills/
            ├── handover/SKILL.md
            ├── read-handover/SKILL.md
            └── verify-loop/
                ├── SKILL.md
                ├── scripts/verify_loop.py
                └── examples/*.json
```

## 初回セットアップ

GitHub に `claude-plugins`（public）を作成してから:

```bash
cd claude-plugins
git init
git add .
git commit -m "Add my plugin with handover skills"
git branch -M main
git remote add origin git@github.com:tei0110/claude-plugins.git
git push -u origin main
```

## インストール

### Claude Code

```
/plugin marketplace add tei0110/claude-plugins
/plugin install my@tei0110
```

インストール後、既存の `~/.claude/commands/my/handover.md` と `~/.claude/commands/my/read-handover.md` は重複するため削除する。

### Cowork（アカウントごとに実施）

同じマーケットプレイスを追加してインストールする。アカウントごとに1回ずつ必要。

## 更新

SKILL.md を編集して push したあと、各環境でプラグインを更新する。`plugin.json` の `version` を上げると更新が確実に反映される。

```bash
git add . && git commit -m "Update handover skill" && git push
```
