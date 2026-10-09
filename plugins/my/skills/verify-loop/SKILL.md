---
name: verify-loop
description: 「実装→検証→失敗したら直す」を検証が通るまで自動で回す自己検証ループの設定・ON/OFF。テストが通るまで終わらせたいとき、プロジェクトに検証ループを導入したいときに使う。
argument-hint: init | on [--set KEY=VALUE ...] [--skip STEP] [--max N] | off | status
---

# verify-loop（自己検証ループ）

このプラグインの Stop hook は、ON にしたセッションで Claude が終了しようとするたびに、
プロジェクトの `.claude/verify-loop.config.json` に書かれた検証を実行する。失敗すると出力を添えて差し戻し、
修正を続けさせる（上限 `max_attempts` 回。超えたら未解決内容を報告させて止める）。
設定ファイルが無いプロジェクト、または OFF のときは何もしない。

スクリプト: このスキルのベースディレクトリにある `scripts/verify_loop.py`（以下 `$VL`）。
`python3 "$VL" <サブコマンド>` で、対象プロジェクトのルートをカレントにして実行する。

引数: `$ARGUMENTS`

## サブコマンド別の手順

### `init` — 設定ファイルを作る

1. 既に `.claude/verify-loop.config.json` がある場合は内容を示し、上書きするか確認する。
2. プロジェクトの CLAUDE.md / AGENTS.md / docs、`package.json`・`composer.json`・`pubspec.yaml`・`Makefile`・CI 設定を読み、
   **プロジェクトが定めている正規のコマンド**（lint・静的解析・テスト・E2E、Docker 経由かどうか、worktree 用の手順、
   テストDB の排他ルールなど）を把握する。推測で独自コマンドを作らない。
3. `examples/` の近いもの（`laravel-docker.json` / `flutter.json` / `node.json`）を土台に設定を書く。
   スキーマは下の「設定ファイル」参照。プロジェクト固有の禁止事項は `rules` に入れる。
4. `python3 "$VL" validate` と `python3 "$VL" status` で確認し、終了時に何が走るかをユーザーに示す。
5. このファイルをコミットするか（チーム共有）、`.git/info/exclude` に追加して個人用にするかはユーザーに確認する。

### `on` — 有効化

- オプションが無い場合は、今回の作業（会話・Issue・差分）から対象テストを推定し、
  設定の `args_key` に合わせて `--set` を組み立てる（例: `--set phpunit="--filter FooTest" --set spec=tests/e2e/a.spec.cjs`）。
  推定内容を1〜2行でユーザーに示してから実行する。全件で良い場合は `--set` を付けない。
- `python3 "$VL" on <オプション>` を実行し、出力された「終了時に実行される検証」をそのまま伝える。
- 以後、作業の完了条件に「Stop hook の検証が通ること」が加わる。差し戻されたら、示されたルールに従って原因を特定して直す。
  **期待値の書き換えで通すことはしない。**

### `off` / `status`

`python3 "$VL" off` / `python3 "$VL" status` を実行して結果を伝える。

## 設定ファイル `.claude/verify-loop.config.json`

worktree では、worktree 自身 → 本体 checkout の順に探す（本体に1つ置けば全 worktree で使える）。

```jsonc
{
  "max_attempts": 3,                 // 差し戻し回数の上限
  "rules": ["..."],                  // 差し戻し時に毎回伝えるプロジェクト固有ルール
  "preflight": [                     // 検証前の事前条件。失敗したら1回だけ伝えて止まる
    { "name": "...", "run": "シェルコマンド", "message": "...", "worktree": false }
  ],
  "post_edit": [                     // ファイル編集直後のチェック（失敗すると即座に Claude へ返る）
    { "name": "...", "files": "*.php", "run": "php -l {file}",
      "always": true,                // true: OFF でも実行（軽いものだけ）
      "if_command": "php",           // そのコマンドが無ければスキップ
      "strip_prefix": "src/", "worktree": false, "message": "...", "timeout": 120 }
  ],
  "steps": [                         // Stop 時に上から順に実行。最初の失敗で止める
    { "name": "...", "run": "シェルコマンド",
      "files": ["src/*.php"],        // 変更ファイル（HEAD 比較＋未追跡）でこの glob に合うものを {files} に展開。無ければスキップ
      "strip_prefix": "src/",
      "args_key": "phpunit",         // on --set phpunit=... の値を {args} に展開
      "args_prefix": "--",           // args があるときだけ前に付ける
      "requires_args": true,         // args 指定が無ければスキップ
      "worktree_run": "...",         // worktree ではこちらを実行。null なら worktree ではスキップ
      "hint": "...", "timeout": 3000 }
  ]
}
```

- glob は Python の fnmatch（`*` は `/` もまたぐ）。パスはリポジトリルート相対。
- コマンドはリポジトリルートをカレントに `sh` で実行される。
- 前回 PASS 時から作業ツリーが変わっていなければ、検証は再実行しない。
- 状態は `.claude/verify-loop.state.json`、最新ログは `.claude/verify-loop-last.log`（どちらも自動で `.git/info/exclude` に追加）。
