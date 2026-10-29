"""
通过 GitHub Git Data API 推送本地提交。

## 为什么需要这个脚本

本机 Clash 订阅的 52 个代理节点全部失效，github.com 直连 TLS 被重置，
`git push` 无法完成。但 `api.github.com` 直连可达且 token 有效，
因此改用 Git Data API 完成同样的推送。

## 做法

不采用"逐文件创建提交"的粗糙方式，而是**复现本地提交对象**：
用本地 blob 内容建 blob、以父提交的树为 base_tree 建树、
按本地提交原样的 author/committer/时间戳/提交信息建 commit。
Git 的提交 SHA 是内容寻址的，若各字段完全一致，得到的 SHA 应与本地一致，
从而保证本地与远程不产生分叉。

## 用法

    python scripts/push_via_api.py --dry-run   # 只做校验，不写入
    python scripts/push_via_api.py             # 实际推送
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OWNER = "DongFFF"
REPO = "procurement-review-system"
API = "https://api.github.com"


def git(*args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout


def token() -> str:
    r = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, encoding="utf-8"
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError("取不到 gh token")
    return r.stdout.strip()


def api(method: str, path: str, payload: dict | None = None, tok: str = "") -> dict:
    url = f"{API}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "procurement-review-push",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {body}") from e


def parse_commit(sha: str) -> dict:
    """解析本地提交对象的各个字段。"""
    raw = git("cat-file", "commit", sha)
    head, _, message = raw.partition("\n\n")
    fields = {}
    for line in head.splitlines():
        k, _, v = line.partition(" ")
        fields.setdefault(k, []).append(v)

    def ident(s: str) -> dict:
        """
        解析 git 的身份行：`Name <email> <epoch> <±HHMM>`。

        注意 epoch 与时区是两个空格分隔的字段，
        早期版本用 rpartition 只切出时区，导致时间戳错乱为 1970 年。
        """
        m = re.match(r"^(.*?) <(.*?)> (\d+) ([+-]\d{4})$", s.strip())
        if not m:
            raise RuntimeError(f"无法解析身份行：{s!r}")
        name, email, epoch, offset = m.groups()
        sign = 1 if offset[0] == "+" else -1
        off = __import__("datetime").timedelta(
            seconds=sign * (int(offset[1:3]) * 3600 + int(offset[3:5]) * 60)
        )
        dt = datetime.fromtimestamp(int(epoch), tz=timezone(off))
        return {"name": name, "email": email, "date": dt.isoformat()}

    return {
        "tree": fields["tree"][0],
        "parents": fields.get("parent", []),
        "author": ident(fields["author"][0]),
        "committer": ident(fields["committer"][0]),
        "message": message,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    local_sha = git("rev-parse", "HEAD").strip()
    info = parse_commit(local_sha)

    print("=" * 78)
    print("GitHub Git Data API 推送")
    print("=" * 78)
    print(f"本地提交: {local_sha}")
    print(f"  父提交: {info['parents'][0] if info['parents'] else '(无)'}")
    print(f"  树:     {info['tree']}")
    print(f"  作者:   {info['author']['name']} <{info['author']['email']}> {info['author']['date']}")
    print(f"  信息首行: {info['message'].splitlines()[0]}")

    tok = token()

    # 远程状态
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{args.branch}", tok=tok)
    remote_sha = ref["object"]["sha"]
    print(f"\n远程 {args.branch}: {remote_sha}")
    if remote_sha == local_sha:
        print("远程已与本地一致，无需推送")
        return 0
    if not info["parents"] or remote_sha != info["parents"][0]:
        print(f"[拒绝] 远程不是本地提交的父节点，非快进，需人工处理")
        return 1

    # 父提交的树
    parent_commit = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{remote_sha}", tok=tok)
    base_tree = parent_commit["tree"]["sha"]

    # 变更文件
    diff = git("diff", "--name-status", f"{remote_sha}..{local_sha}").strip().splitlines()
    changes = []
    for line in diff:
        parts = line.split("\t")
        status, path = parts[0], parts[-1]
        changes.append((status, path))
    print(f"\n变更文件 {len(changes)} 个:")
    for s, p in changes:
        print(f"  {s}  {p}")

    if args.dry_run:
        print("\n[dry-run] 到此为止")
        return 0

    # 建 blob
    print("\n创建 blob…")
    entries = []
    for status, path in changes:
        if status == "D":
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        content = (ROOT / path).read_bytes()
        b = api(
            "POST",
            f"/repos/{OWNER}/{REPO}/git/blobs",
            {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
            tok=tok,
        )
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": b["sha"]})
        print(f"  {path[:56]:58} {b['sha'][:10]}")

    # 建树
    tree = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/git/trees",
        {"base_tree": base_tree, "tree": entries},
        tok=tok,
    )
    print(f"\n新树: {tree['sha']}")
    if tree["sha"] != info["tree"]:
        print(f"[警告] 树的 SHA 与本地不一致（本地 {info['tree']}）")
    else:
        print("树的 SHA 与本地一致")

    # 建提交
    commit = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/git/commits",
        {
            "message": info["message"],
            "tree": tree["sha"],
            "parents": [remote_sha],
            "author": info["author"],
            "committer": info["committer"],
        },
        tok=tok,
    )
    print(f"\n新提交: {commit['sha']}")
    if commit["sha"] == local_sha:
        print("提交 SHA 与本地一致 —— 本地与远程完全同步")
    else:
        print(f"[注意] 提交 SHA 与本地不同（本地 {local_sha}），需后续对齐")

    # 更新分支
    api(
        "PATCH",
        f"/repos/{OWNER}/{REPO}/git/refs/heads/{args.branch}",
        {"sha": commit["sha"], "force": False},
        tok=tok,
    )
    print(f"\n已更新 {args.branch} -> {commit['sha']}")

    # 复核
    ref2 = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{args.branch}", tok=tok)
    print(f"复核：远程 {args.branch} 现为 {ref2['object']['sha']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
