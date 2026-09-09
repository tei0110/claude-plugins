---
name: read-handover
description: 前回のセッションの引き継ぎ資料を探して読み込み、次にやることと作業環境を要約する。作業を再開するとき、引き継ぎを受けるときに使う。
disable-model-invocation: true
---

# セッション引き継ぎ資料の読み込み

前回のセッションの引き継ぎ資料を読み込む。
`$ARGUMENTS` で読み込むファイルを指定する（指定があればそれを優先）。

## 0. 添付ファイルの優先チェック

チャットに引き継ぎ資料らしきファイルが添付されている場合は、スキャンより先にそれを読む。別アカウント・別環境からの引き継ぎはこの経路になる。

## 1. スキャン対象（worktree 統一対応）

メイン worktree とすべての worktree 内 `claudedocs/handover/` を横断スキャンする。Coworkでフォルダを接続している場合は、ユーザーのマシン上のシェルで実行する。シェルが使えない場合はその旨を伝えて添付を依頼する。

```bash
# メイン worktree のパス
MAIN_WT=$(git worktree list 2>/dev/null | head -1 | awk '{print $1}')

# mtime を取る（GNU stat → BSD stat の順にフォールバック）
mt() { stat -c "%Y %n" "$1" 2>/dev/null || stat -f "%m %N" "$1" 2>/dev/null; }

# zsh の glob nomatch を避けるため find で展開（mtime 新しい順）
# 対象:
# 1. メイン実行の最新 (latest.md)
# 2. メイン保存・worktree識別子付き最新 (latest_wt-*.md) ← 新書き込み統一後
# 3. 旧仕様: 各 worktree 内に直接書かれた latest.md ← 過去資料の互換性のため
{
  find "$MAIN_WT/claudedocs/handover" -maxdepth 1 \( -name 'latest.md' -o -name 'latest_wt-*.md' \) 2>/dev/null
  find "$MAIN_WT/.worktrees" -mindepth 4 -maxdepth 4 -path '*/claudedocs/handover/latest.md' 2>/dev/null
} | while read -r f; do
  mt "$f"
done | sort -rn | cut -d' ' -f2-
```

メイン worktree が git 管理外の場合（`git worktree list` が失敗する）は、現在地を起点に従来の優先順位（`./claudedocs/handover` → `./docs/handover` → `./.claude/handover`）で `latest.md` を探す。

## 2. ファイルが見つかった件数による分岐

**0 件**: 引き継ぎ資料がない旨をユーザーに通知して終了。

**1 件**: そのまま読み込む。読み込み後、ファイルパス（メイン保存 / worktree保存どちらか）を冒頭に明示する。

**2 件以上**: 選択肢を提示してユーザーに選ばせる。

```
質問: 複数の引き継ぎ資料が見つかりました。どれを読みますか？

選択肢の例:
  - [メイン] latest.md (最終更新: 2026-06-04 14:55)
    20260604_145509_sample-app_ISSUE-1947_レビュー対応_詳細画面完了_一覧編集中断.md
  - [worktree: detail-design] latest_wt-detail-design.md (最終更新: 2026-06-04 15:12)
    20260604_151245_sample-app_詳細設計_Task9途中_バリデーション差分ドラフト承認待ち.md
  - [全部読む] 直近順に全件を連続読み込み
```

最終更新日時は `ls -lt` または `stat` で取得し、新しい順に並べる。選択肢のラベルは短くし、ファイル名全体は説明側に入れる。

## 3. `$ARGUMENTS` で指定がある場合

- ファイル名のみ（例: `20260604_151245_xxx.md`）: メイン → 全worktree の順で探して最初に見つかったものを読む
- 相対パス: 現在地基準で読む
- 絶対パス: そのまま読む

## 4. 読み込み後の対応

- 引き継ぎ資料の冒頭に書かれた「次セッションで最初にやること」「作業環境（worktree パス・ブランチ・HEAD）」を抽出してユーザーに要約提示
- worktree 指定がある場合、現在地が一致しているか確認（不一致なら警告）
- 別環境で作られた資料の場合、記載されたパスやリンクがこのセッションから到達できるか確認し、到達できないものはユーザーに伝える
