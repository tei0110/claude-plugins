#!/usr/bin/env python3
"""verify-loop: 「検証が通るまで Claude を終了させない」汎用 hook。

プロジェクトごとの設定 `.claude/verify-loop.config.json` に検証手順（シェルコマンド）を書き、
`on` で有効化したセッションだけ Stop hook が検証を走らせる。設定が無いプロジェクトでは何もしない。

サブコマンド:
  on [--set KEY=VALUE ...] [--skip STEP ...] [--max N]   有効化（KEY は各 step の args_key）
  off | status | validate
  post-edit     PostToolUse hook 用
  stop          Stop hook 用
"""
import argparse
import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

CONFIG = ".claude/verify-loop.config.json"
STATE = ".claude/verify-loop.state.json"
LOG = ".claude/verify-loop-last.log"
DEFAULT_MAX = 3
DEFAULT_TIMEOUT = 3000
TAIL = 80
DEFAULT_RULES = [
    "テストを通すために期待値やアサーションを書き換えない。仕様が怪しい場合は修正せずユーザーに確認する",
    "失敗の原因を特定してから直す（当て推量で複数箇所を変えない）",
]


# ---------- 共通 ----------
def run(cmd, cwd, timeout=DEFAULT_TIMEOUT, env=None, shell=False):
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        return 124, out + f"\n[timeout {timeout}s]"
    except FileNotFoundError as e:
        return 127, str(e)


def git(root, *args):
    rc, out = run(["git", *args], root, timeout=60)
    return out.strip() if rc == 0 else ""


def project_root(hook_input=None):
    cand = os.environ.get("CLAUDE_PROJECT_DIR") or (hook_input or {}).get("cwd") or os.getcwd()
    return git(cand, "rev-parse", "--show-toplevel") or cand


def main_root(root):
    """worktree なら本体 checkout のルート（設定ファイルのフォールバック先）"""
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.dirname(common) if common.endswith("/.git") else root


def is_worktree(root):
    gd = git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    cd = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return bool(gd and cd and gd != cd)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config(root):
    """worktree 自身 → 本体 checkout の順で設定を探す"""
    for base in (root, main_root(root)):
        cfg = load(os.path.join(base, CONFIG))
        if cfg is not None:
            return cfg
    return None


def tail(text, n=TAIL):
    return "\n".join(text.rstrip().splitlines()[-n:])


def changed_files(root):
    names = set(git(root, "diff", "--name-only", "HEAD").splitlines())
    names |= set(git(root, "ls-files", "--others", "--exclude-standard").splitlines())
    return sorted(n for n in names if n and os.path.isfile(os.path.join(root, n)))


def file_hash(root, rel):
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def baseline(root):
    """ON にした時点で変更済み・未追跡だったファイルとその内容ハッシュ"""
    return {f: file_hash(root, f) for f in changed_files(root)}


def target_files(root, st):
    """チェック対象: ON 以降に新しく現れたファイル、または ON 時点から内容が変わったファイル"""
    base = st.get("baseline") or {}
    return [f for f in changed_files(root) if f not in base or base[f] != file_hash(root, f)]


def match(path, patterns):
    pats = [patterns] if isinstance(patterns, str) else patterns
    return any(fnmatch.fnmatch(path, p) for p in pats)  # * は / もまたぐ


def fingerprint(root):
    stash = git(root, "stash", "create")
    tree = git(root, "rev-parse", (stash or "HEAD") + "^{tree}")
    h = hashlib.sha256(tree.encode())
    for n in sorted(git(root, "ls-files", "--others", "--exclude-standard").splitlines()):
        h.update(n.encode())
        try:
            with open(os.path.join(root, n), "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except Exception:
            pass
    return h.hexdigest()


def ensure_excluded(root):
    """状態ファイルとログを git の管理外にする"""
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return
    ex = os.path.join(common, "info", "exclude")
    try:
        cur = open(ex, encoding="utf-8").read() if os.path.exists(ex) else ""
        if STATE not in cur:
            os.makedirs(os.path.dirname(ex), exist_ok=True)
            with open(ex, "a", encoding="utf-8") as f:
                f.write(f"\n# verify-loop\n{STATE}\n{LOG}\n")
    except Exception:
        pass


def expand(cmd, files=None, args=None, file=None):
    return (cmd.replace("{files}", " ".join(shlex.quote(f) for f in (files or [])))
               .replace("{args}", " ".join(shlex.quote(a) for a in (args or [])))
               .replace("{file}", shlex.quote(file or "")))


# ---------- on / off / status / validate ----------
def cmd_on(argv):
    ap = argparse.ArgumentParser(prog="verify_loop.py on")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="step の args_key に渡す引数（同じ KEY を繰り返すと追加）")
    ap.add_argument("--skip", action="append", default=[], metavar="STEP", help="今回スキップする step 名")
    ap.add_argument("--max", type=int, default=None)
    a = ap.parse_args(argv)
    root = project_root()
    cfg = load_config(root)
    if cfg is None:
        print(f"設定ファイル {CONFIG} がありません。先に作成してください（/my:verify-loop init）。")
        return 1
    args = {}
    for kv in a.set:
        if "=" not in kv:
            print(f"--set は KEY=VALUE 形式で指定してください: {kv}")
            return 1
        k, v = kv.split("=", 1)
        args.setdefault(k, []).extend(shlex.split(v))
    keys = {s.get("args_key") for s in cfg.get("steps", []) if s.get("args_key")}
    unknown = set(args) - keys
    if unknown:
        print(f"未定義の KEY: {sorted(unknown)}（使える KEY: {sorted(keys)}）")
        return 1
    save(os.path.join(root, STATE), {
        "enabled": True, "args": args, "skip": a.skip,
        "max_attempts": a.max or cfg.get("max_attempts", DEFAULT_MAX),
        "attempts": 0, "enabled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "baseline": baseline(root)})
    ensure_excluded(root)
    print("verify-loop: ON")
    return cmd_status([])


def cmd_off(_):
    root = project_root()
    st = load(os.path.join(root, STATE))
    if st:
        save(os.path.join(root, STATE), {"enabled": False})
    print("verify-loop: OFF")
    return 0


def plan(cfg, st, root, wt):
    """各 step を実行するか・何を実行するかを決める -> [(name, cmd or None, 理由)]"""
    out = []
    changed = target_files(root, st)
    for s in cfg.get("steps", []):
        name = s["name"]
        if name in st.get("skip", []):
            out.append((name, None, "--skip 指定"))
            continue
        cmd = s["run"]
        if wt and "worktree_run" in s:
            if s["worktree_run"] is None:
                out.append((name, None, "worktree では実行しない設定"))
                continue
            cmd = s["worktree_run"]
        files = None
        if s.get("files"):
            files = [f for f in changed if match(f, s["files"])]
            if not files:
                out.append((name, None, "対象の変更ファイルなし"))
                continue
            pre = s.get("strip_prefix")
            if pre:
                files = [f[len(pre):] if f.startswith(pre) else f for f in files]
        args = None
        if s.get("args_key"):
            args = st.get("args", {}).get(s["args_key"], [])
            if not args and s.get("requires_args"):
                out.append((name, None, f"--set {s['args_key']}=... の指定なし"))
                continue
            if args and s.get("args_prefix"):
                args = shlex.split(s["args_prefix"]) + args
        out.append((name, expand(cmd, files=files, args=args), ""))
    return out


def cmd_status(_):
    root = project_root()
    cfg = load_config(root)
    st = load(os.path.join(root, STATE), {}) or {}
    wt = is_worktree(root)
    print(f"root: {root}  worktree: {wt}")
    print(f"config: {'あり' if cfg is not None else 'なし'}  状態: {'ON' if st.get('enabled') else 'OFF'}")
    if cfg is None:
        return 0
    if st.get("enabled"):
        print(f"試行: {st.get('attempts', 0)}/{st.get('max_attempts')}  args: {json.dumps(st.get('args', {}), ensure_ascii=False)}")
        n = len(st.get("baseline") or {})
        print(f"ON 時点で変更済み・未追跡だったファイル {n} 件は、内容が変わらない限りチェック対象外")
    else:
        st = {**st, "baseline": baseline(root)}
        print(f"今 ON にすると、変更済み・未追跡のファイル {len(st['baseline'])} 件は内容が変わらない限りチェック対象外になります")
    print("終了時に実行される検証:")
    for name, cmd, why in plan(cfg, st, root, wt):
        print(f"  - {name}: {cmd if cmd else '（スキップ: ' + why + '）'}")
    return 0


def cmd_validate(_):
    root = project_root()
    cfg = load_config(root)
    if cfg is None:
        print(f"{CONFIG} がありません")
        return 1
    errs = []
    names = set()
    for i, s in enumerate(cfg.get("steps", [])):
        if not s.get("name") or not s.get("run"):
            errs.append(f"steps[{i}]: name と run は必須")
        if s.get("name") in names:
            errs.append(f"steps[{i}]: name が重複 ({s.get('name')})")
        names.add(s.get("name"))
        if "{files}" in s.get("run", "") and not s.get("files"):
            errs.append(f"steps[{i}]: run に {{files}} があるのに files（glob）が無い")
        if "{args}" in s.get("run", "") and not s.get("args_key"):
            errs.append(f"steps[{i}]: run に {{args}} があるのに args_key が無い")
    for i, p in enumerate(cfg.get("post_edit", [])):
        if not p.get("files") or not p.get("run"):
            errs.append(f"post_edit[{i}]: files と run は必須")
    print("\n".join(errs) if errs else "OK")
    return 1 if errs else 0


# ---------- PostToolUse ----------
def cmd_post_edit(_):
    data = json.load(sys.stdin)
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or not os.path.isfile(path):
        return 0
    root = project_root(data)
    cfg = load_config(root)
    if cfg is None:
        return 0
    on = (load(os.path.join(root, STATE), {}) or {}).get("enabled")
    rel = os.path.relpath(os.path.abspath(path), root)
    wt = is_worktree(root)
    for p in cfg.get("post_edit", []):
        if not match(rel, p["files"]) or (not p.get("always") and not on):
            continue
        if p.get("if_command") and not shutil.which(p["if_command"]):
            continue
        if wt and p.get("worktree") is False:
            continue
        f = rel[len(p["strip_prefix"]):] if p.get("strip_prefix") and rel.startswith(p["strip_prefix"]) else rel
        rc, out = run(expand(p["run"], file=f), root, timeout=p.get("timeout", 120), shell=True)
        if rc != 0:
            msg = p.get("message", "編集後チェックが失敗しました。修正してください。")
            print(f"[{p.get('name', 'post-edit')}] {msg}\n{tail(out, 20)}", file=sys.stderr)
            return 2
    return 0


# ---------- Stop ----------
def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def cmd_stop(_):
    data = json.load(sys.stdin)
    root = project_root(data)
    st_path = os.path.join(root, STATE)
    st = load(st_path, {}) or {}
    if not st.get("enabled") or st.get("exhausted"):
        return 0
    cfg = load_config(root)
    if cfg is None:
        return 0
    fp = fingerprint(root)
    if st.get("last_pass") == fp:
        return 0
    wt = is_worktree(root)
    log = [f"# verify-loop {time.strftime('%Y-%m-%d %H:%M:%S')} root={root} worktree={wt}"]

    # 事前条件（コンテナ起動など）。満たさなければ1回だけ伝えて止まる
    for pf in cfg.get("preflight", []):
        if wt and pf.get("worktree") is False:
            continue
        rc, out = run(pf["run"], root, timeout=pf.get("timeout", 60), shell=True)
        if rc != 0:
            save(st_path, {**st, "exhausted": True})
            return block(f"verify-loop: 事前条件「{pf.get('name', pf['run'])}」を満たしていないため検証できません。"
                         f"{pf.get('message', '')} 解消後に /my:verify-loop on で再開できる旨をユーザーに伝えて終了してください。")

    failed = None
    for s, (name, cmd, why) in zip(cfg.get("steps", []), plan(cfg, st, root, wt)):
        if cmd is None:
            log.append(f"\n## {name}\nスキップ: {why}")
            continue
        log.append(f"\n## {name}\n$ {cmd}")
        rc, out = run(cmd, root, timeout=s.get("timeout", DEFAULT_TIMEOUT), shell=True)
        log.append(out)
        if rc != 0:
            failed = (s, out)
            break
    with open(os.path.join(root, LOG), "w", encoding="utf-8") as f:
        f.write("\n".join(log))

    if failed is None:
        save(st_path, {**st, "attempts": 0, "last_pass": fp, "last_pass_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return 0

    s, out = failed
    attempts = int(st.get("attempts", 0)) + 1
    mx = int(st.get("max_attempts", DEFAULT_MAX))
    if attempts > mx:
        save(st_path, {**st, "attempts": attempts, "exhausted": True})
        return block(f"verify-loop: 修正試行が上限（{mx}回）に達しました。これ以上修正せず、未解決の失敗（{s['name']}）、"
                     f"原因の見立て、試したこと、残リスクをユーザーに報告して終了してください。全ログ: {LOG}\n\n{tail(out, 40)}")
    save(st_path, {**st, "attempts": attempts})
    rules = DEFAULT_RULES + cfg.get("rules", []) + ([s["hint"]] if s.get("hint") else [])
    return block(f"verify-loop: {s['name']} が失敗しています（試行 {attempts}/{mx}）。原因を特定して修正してください。\n"
                 "ルール:\n" + "\n".join(f"- {r}" for r in rules) +
                 f"\n全ログ: {LOG}\n\n--- 出力（末尾） ---\n{tail(out)}")


def main():
    sub, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    fn = {"on": cmd_on, "off": cmd_off, "status": cmd_status, "validate": cmd_validate,
          "post-edit": cmd_post_edit, "stop": cmd_stop}.get(sub)
    if fn is None:
        print(__doc__)
        return 1
    try:
        return fn(rest) or 0
    except Exception as e:  # hook の不具合で Claude を止めない
        print(f"verify-loop internal error: {e}", file=sys.stderr)
        return 0 if sub in ("post-edit", "stop") else 1


if __name__ == "__main__":
    sys.exit(main())
