# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-code-transcripts==0.6"]
# ///
"""CC Archive: turn ~/.claude/projects into a browsable local site.

    uv run build.py            # incremental build into ./site
    uv run build.py --open     # build, then open in the browser
    uv run build.py --full     # regenerate every transcript

Session pages come from simonw/claude-code-transcripts; this script adds
the dashboard (index.html) on top. Everything stays on disk, no server.
"""

import argparse
import contextlib
import fcntl
import io
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_code_transcripts import generate_html, make_msg_id

HERE = Path(__file__).resolve().parent
SRC = Path.home() / ".claude" / "projects"
HOME = str(Path.home())
# ponytail: remember-plugin scratch sessions live under /private/...; add patterns here if more noise shows up
EXCLUDE_DIRS = re.compile(r"^-private-")
SKIP_PROMPT = re.compile(
    r"^(<local-command-|<system-reminder>|\[Request interrupted|Caveat:|<command-message>|<task-notification>)"
)
IDLE_GAP = 30 * 60  # gaps longer than this don't count as active time


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


def repo_of(cwd):
    # worktrees (.claude/worktrees/<name>) belong to their parent repo
    return cwd.split("/.claude/worktrees/")[0].rstrip("/") if cwd else ""


def nice_path(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def session_title(s):
    # custom /rename > agent name > Claude's auto title > first prompt
    for t in (s["customTitle"], s["agentName"], s["aiTitle"]):
        if t:
            return t
    first = s["prompts"][0]["text"] if s["prompts"] else "(no prompt)"
    return first.splitlines()[0][:80]


def parse_session(path):
    s: dict[str, Any] = dict(
        id=path.stem, customTitle="", agentName="", aiTitle="", cwd="", entry="", branches=[],
        models=[], prompts=[], compacts=[], recaps=[], prs={}, outTokens=0, times=[],
    )
    seen_msg = set()
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, ts = e.get("type"), e.get("timestamp")
        if t == "custom-title":
            s["customTitle"] = e.get("customTitle", "")
        elif t == "agent-name":
            s["agentName"] = e.get("agentName", "")
        elif t == "ai-title":
            s["aiTitle"] = e.get("aiTitle", "")
        elif t == "pr-link" and e.get("prUrl"):
            s["prs"][e["prUrl"]] = {"url": e["prUrl"], "num": e.get("prNumber"), "repo": e.get("prRepository", "")}
        if e.get("isSidechain"):
            continue
        if ts:
            s["times"].append(ts)
        if not s["cwd"] and e.get("cwd"):
            s["cwd"] = e["cwd"]
        if not s["entry"] and e.get("entrypoint"):
            s["entry"] = e["entrypoint"]  # cli / claude-desktop / sdk-*
        b = e.get("gitBranch")
        if b and b != "HEAD" and b not in s["branches"]:
            s["branches"].append(b)

        if t == "system" and e.get("subtype") == "compact_boundary":
            m = e.get("compactMetadata") or {}
            s["compacts"].append(dict(ts=ts, trigger=m.get("trigger", ""), pre=m.get("preTokens"),
                                      post=m.get("postTokens"), summary=""))
        elif t == "system" and e.get("subtype") == "away_summary":
            txt = re.sub(r"\s*\(disable recaps in /config\)\s*$", "", str(e.get("content", "")))
            s["recaps"].append({"ts": ts, "text": txt})
        elif t == "user" and e.get("isCompactSummary"):
            if not s["compacts"] or s["compacts"][-1]["summary"]:
                s["compacts"].append(dict(ts=ts, trigger="", pre=None, post=None, summary=""))
            c = s["compacts"][-1]
            c["summary"], c["anchor"] = text_of(e["message"].get("content")), make_msg_id(ts or "")
        elif t == "user" and not e.get("isMeta"):
            txt = text_of(e.get("message", {}).get("content")).strip()
            cmd = re.match(r"<command-name>(/\S+)</command-name>", txt)
            if cmd:
                txt = cmd.group(1)
            elif not txt or SKIP_PROMPT.match(txt):
                continue
            s["prompts"].append({"ts": ts, "text": txt[:600]})
        elif t == "assistant":
            msg = e.get("message", {})
            if msg.get("model") and msg["model"] not in s["models"] and not msg["model"].startswith("<"):
                s["models"].append(msg["model"])
            mid = msg.get("id")
            if mid and mid not in seen_msg:
                seen_msg.add(mid)
                s["outTokens"] += (msg.get("usage") or {}).get("output_tokens", 0) or 0
    return s


def summarize(s, src_dir):
    times = sorted(s.pop("times"))
    if not times or not s["prompts"]:
        return None
    dts = [parse_ts(t) for t in times]
    active = sum(min((b - a).total_seconds(), IDLE_GAP) for a, b in zip(dts, dts[1:])
                 if (b - a).total_seconds() < IDLE_GAP)
    days = {}
    for p in s["prompts"]:
        if p["ts"]:
            d = parse_ts(p["ts"]).strftime("%Y-%m-%d")
            days[d] = days.get(d, 0) + 1
    repo = repo_of(s["cwd"]) or src_dir.name
    s.update(
        title=session_title(s), start=dts[0].isoformat(), end=dts[-1].isoformat(),
        activeMin=round(active / 60), days=days, prs=list(s["prs"].values()),
        repo=nice_path(repo), project="home" if repo == HOME else Path(repo).name or repo,
        group="~" if repo == HOME else nice_path(str(Path(repo).parent)),
        worktree="/.claude/worktrees/" in s["cwd"],
    )
    return s


def find_page(out_dir, anchor):
    for page in sorted(out_dir.glob("page-*.html")):
        if f'id="{anchor}"' in page.read_text(encoding="utf-8", errors="ignore"):
            return page.name
    return "index.html"


def build_transcript(jsonl, out_dir, sid):
    out_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        generate_html(jsonl, out_dir)
    # add a way back to the dashboard on every transcript page
    bar = (f'<a href="../../index.html#s={sid}" style="position:fixed;top:12px;left:12px;z-index:99;'
           'font:600 13px/1 system-ui;padding:8px 12px;border-radius:999px;background:#111;color:#fff;'
           'text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.25)">← CC Archive</a>')
    for page in out_dir.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        page.write_text(re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + bar, html, count=1), encoding="utf-8")


def write_atomic(path, text):
    # the dashboard may be open while a hook rebuilds; never expose a half-written file
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "site")
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--full", action="store_true", help="regenerate all transcripts")
    ap.add_argument("--open", action="store_true", help="open the dashboard when done")
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)  # transcripts may hold secrets; keep them private to this user
    # ponytail: a second build that finds the lock just skips; the next SessionEnd catches up
    lock = (out / ".lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another build is running, skipped")
        return
    cache_file = out / "cache.json"
    # --full still loads the cache: it's the only copy of sessions Claude Code already deleted
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    files = [f for d in sorted(args.src.iterdir()) if d.is_dir() and not EXCLUDE_DIRS.match(d.name)
             for f in sorted(d.glob("*.jsonl")) if not f.name.startswith("agent-")]
    sessions, built, failed = [], 0, 0
    for i, f in enumerate(files, 1):
        st = f.stat()
        key = f"{st.st_mtime_ns}:{st.st_size}"
        hit = None if args.full else cache.get(str(f))
        if hit and hit["key"] == key:
            if hit["session"]:
                sessions.append(hit["session"])
            continue
        if sys.stderr.isatty():
            print(f"\r[{i}/{len(files)}] {f.parent.name[-50:]:<50}", end="", file=sys.stderr, flush=True)
        s = summarize(parse_session(f), f.parent)
        if s:
            tdir = out / "s" / s["id"]
            try:
                build_transcript(f, tdir, s["id"])
                for c in s["compacts"]:
                    if c.get("anchor"):
                        c["page"] = find_page(tdir, c["anchor"])
                s["transcript"] = True
                built += 1
            except Exception as ex:  # one broken session shouldn't stop the archive
                s["transcript"], s["error"] = False, str(ex)[:200]
                failed += 1
            sessions.append(s)
        cache[str(f)] = {"key": key, "session": s}
    print(file=sys.stderr)

    # Claude Code deletes transcripts after cleanupPeriodDays; the archive keeps them
    live = {str(f) for f in files}
    for k, v in cache.items():
        if k not in live and v["session"]:
            v["session"]["archived"] = True
            sessions.append(v["session"])
    write_atomic(cache_file, json.dumps(cache, ensure_ascii=False))

    sessions.sort(key=lambda s: s["end"], reverse=True)
    data = {"generated": datetime.now().astimezone().isoformat(), "sessions": sessions}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    write_atomic(out / "index.html", tpl.replace("/*__DATA__*/null", payload))

    print(f"{len(sessions)} sessions · {built} rebuilt · {failed} failed → {out / 'index.html'}")
    if args.open:
        webbrowser.open((out / "index.html").as_uri())


if __name__ == "__main__":
    main()
