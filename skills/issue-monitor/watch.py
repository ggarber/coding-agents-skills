#!/usr/bin/env python3
"""Watch a GitHub repo and print one line per new issue, comment, review comment or commit.

Two ways to learn about changes, used together:
  - push: `gh webhook forward` (the cli/gh-webhook extension) relays the repo's webhooks to a
    local port, and each delivery is reported straight from its payload (1-2 s latency).
  - poll: conditional requests (ETag / If-None-Match). An unchanged answer is a 304, which
    does not count against GitHub's rate limit, so polling every few seconds is cheap. It
    catches anything the webhooks missed (forwarder down, events from before the start).
Both paths share one record of what was reported, so nothing is printed twice.

Output lines (stdout, one per event):
  NEW_ISSUE #N [state] by USER: TITLE
  NEW_COMMENT #N [state] by USER (id ID): BODY
  NEW_REVIEW_COMMENT #N by USER (id ID) PATH:LINE: BODY
  NEW_COMMIT BRANCH SHA by AUTHOR: MESSAGE
Status and errors go to stderr.
"""
import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_MARKER = "<!-- claude-issue-monitor -->"
EVENTS = "issues,issue_comment,pull_request_review_comment,push"


def log(msg):
    print(f"[watch] {msg}", file=sys.stderr, flush=True)


def emit(line):
    print(line, flush=True)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def one_line(text, limit=200):
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


class State:
    def __init__(self, path, branches):
        self.path = path
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        start = iso(datetime.now(timezone.utc) - timedelta(days=1))
        self.since = data.get("since", {})
        for key in ["issues", "comments", "review_comments"] + [f"commits:{b}" for b in branches]:
            self.since.setdefault(key, start)
        self.fresh = not data
        self.seen = data.get("seen", [])
        self._seen = set(self.seen)
        self.etags = data.get("etags", {})

    def is_new(self, key):
        if key in self._seen:
            return False
        self._seen.add(key)
        self.seen.append(key)
        return True

    def save(self):
        self.seen = self.seen[-5000:]
        self._seen = set(self.seen)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"since": self.since, "seen": self.seen, "etags": self.etags}, f)
        os.replace(tmp, self.path)


class Watcher:
    def __init__(self, args):
        self.repo = args.repo
        self.marker = args.marker
        self.react = not args.no_react
        self.branches = args.branches
        state_dir = os.path.expanduser(args.state_dir)
        os.makedirs(state_dir, exist_ok=True)
        self.state = State(os.path.join(state_dir, self.repo.replace("/", "_") + ".json"), self.branches)

    def get(self, key, path, params):
        """GET with If-None-Match. Returns the parsed list, or None when unchanged or on error."""
        since = self.state.since[key]
        url = f"repos/{self.repo}/{path}?{params}&since={since}&per_page=100"
        args = ["api", "-i", url]
        etag = self.state.etags.get(key)
        if etag and etag.get("url") == url:
            args += ["-H", f"If-None-Match: {etag['etag']}"]
        out = gh(*args)
        status = re.match(r"HTTP/\S+ (\d+)", out.stdout)
        code = int(status.group(1)) if status else 0
        if code == 304:
            return None
        if code != 200:
            log(f"{path}: HTTP {code or '?'} {one_line(out.stderr)}")
            return None
        head, _, body = out.stdout.partition("\r\n\r\n") if "\r\n\r\n" in out.stdout else out.stdout.partition("\n\n")
        m = re.search(r"(?im)^etag:\s*(.+?)\s*$", head)
        if m:
            self.state.etags[key] = {"url": url, "etag": m.group(1)}
        try:
            return json.loads(body)
        except ValueError:
            log(f"{path}: unreadable body")
            return None

    def advance(self, key, items, started):
        # Only move `since` when something came back, so the URL (and its ETag) stays stable.
        if items:
            self.state.since[key] = started

    def eyes(self, endpoint):
        if self.react:
            gh("api", "-X", "POST", f"repos/{self.repo}/{endpoint}/reactions", "-f", "content=eyes")

    # Webhook payloads and polled API objects share these shapes, so both paths report through them.
    # State is saved right after each line, so a restart never reports the same event twice.

    def issue(self, i):
        if "pull_request" in i or not self.state.is_new(f"issue:{i['number']}"):
            return
        emit(f"NEW_ISSUE #{i['number']} [{i['state']}] by {i['user']['login']}: {one_line(i['title'])}")
        self.state.save()
        self.eyes(f"issues/{i['number']}")

    def comment(self, c, state=None):
        if self.marker in (c.get("body") or "") or not self.state.is_new(f"comment:{c['id']}"):
            return
        num = c["issue_url"].rsplit("/", 1)[-1]
        if state is None:
            state = gh("api", f"repos/{self.repo}/issues/{num}", "--jq", ".state").stdout.strip() or "?"
        emit(f"NEW_COMMENT #{num} [{state}] by {c['user']['login']} (id {c['id']}): {one_line(c['body'])}")
        self.state.save()
        self.eyes(f"issues/comments/{c['id']}")

    def review_comment(self, c):
        if self.marker in (c.get("body") or "") or not self.state.is_new(f"review:{c['id']}"):
            return
        num = c["pull_request_url"].rsplit("/", 1)[-1]
        where = f"{c.get('path')}:{c.get('line') or c.get('original_line') or '?'}"
        emit(f"NEW_REVIEW_COMMENT #{num} by {c['user']['login']} (id {c['id']}) {where}: {one_line(c['body'])}")
        self.state.save()
        self.eyes(f"pulls/comments/{c['id']}")

    def commit(self, branch, sha, author, message, quiet=False):
        if not self.state.is_new(f"commit:{sha}") or quiet:
            return
        emit(f"NEW_COMMIT {branch} {sha[:7]} by {author}: {one_line(message.splitlines()[0] if message else '')}")
        self.state.save()

    def event(self, kind, payload):
        """Report straight from a webhook delivery, without waiting for the API to catch up."""
        action = payload.get("action")
        if kind == "issues" and action in ("opened", "reopened", "transferred"):
            self.issue(payload["issue"])
        elif kind == "issue_comment" and action == "created":
            self.comment(payload["comment"], payload["issue"]["state"])
        elif kind == "pull_request_review_comment" and action == "created":
            self.review_comment(payload["comment"])
        elif kind == "push":
            branch = payload.get("ref", "").replace("refs/heads/", "", 1)
            if branch in self.branches:
                for c in payload.get("commits") or []:
                    a = c.get("author") or {}
                    self.commit(branch, c["id"], a.get("username") or a.get("name") or "?", c.get("message"))

    def check(self):
        started = iso(datetime.now(timezone.utc) - timedelta(seconds=5))

        issues = self.get("issues", "issues", "state=all&sort=updated&direction=asc")
        for i in issues or []:
            self.issue(i)
        self.advance("issues", issues, started)

        comments = self.get("comments", "issues/comments", "sort=updated&direction=asc")
        for c in comments or []:
            self.comment(c)
        self.advance("comments", comments, started)

        reviews = self.get("review_comments", "pulls/comments", "sort=updated&direction=asc")
        for c in reviews or []:
            self.review_comment(c)
        self.advance("review_comments", reviews, started)

        for branch in self.branches:
            key = f"commits:{branch}"
            commits = self.get(key, "commits", f"sha={branch}")
            for c in reversed(commits or []):
                author = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
                # On the very first run, remember existing commits without reporting them.
                self.commit(branch, c["sha"], author, c["commit"]["message"], quiet=self.state.fresh)
            self.advance(key, commits, started)

        self.state.fresh = False
        self.state.save()


FORWARDERS = []


def stop_forwarders(*_):
    # SIGINT lets gh delete its temporary repo hook, so the next start doesn't collide with it.
    for p in list(FORWARDERS):
        p.send_signal(signal.SIGINT)
    for p in list(FORWARDERS):
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


def start_webhook(repo, port, inbox):
    if gh("webhook", "--help").returncode != 0:
        log("gh webhook extension not installed (gh extension install cli/gh-webhook); polling only")
        return False

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.end_headers()
            try:
                inbox.put((self.headers.get("X-GitHub-Event", ""), json.loads(body)))
            except ValueError:
                inbox.put(("", {}))  # unreadable: still worth a check

        def log_message(self, *a):
            pass

    signal.signal(signal.SIGTERM, stop_forwarders)
    signal.signal(signal.SIGINT, stop_forwarders)
    signal.signal(signal.SIGHUP, stop_forwarders)  # terminal window or tmux session closed
    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def forward():
        while True:
            log(f"forwarding webhooks ({EVENTS}) to 127.0.0.1:{server.server_port}")
            p = subprocess.Popen(
                ["gh", "webhook", "forward", f"--repo={repo}", f"--events={EVENTS}",
                 f"--url=http://127.0.0.1:{server.server_port}/"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            FORWARDERS.append(p)
            err = one_line(p.communicate()[1], 300)
            FORWARDERS.remove(p)
            # GitHub keeps a dropped forwarder's hook for a short while; a restart collides with it.
            delay = 15 if "already exists" in err else 60
            log(f"webhook forward exited ({err}); retrying in {delay} s, polling meanwhile")
            time.sleep(delay)

    threading.Thread(target=forward, daemon=True).start()
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("repo", nargs="?", help="owner/repo (default: the repo of the current directory)")
    p.add_argument("--interval", type=float, default=15, help="poll interval in seconds (default 15)")
    p.add_argument("--no-webhook", action="store_true", help="poll only, don't try gh webhook forward")
    p.add_argument("--port", type=int, default=0, help="local port for webhook deliveries (default: any free)")
    p.add_argument("--branches", default=None, help="comma-separated branches to report commits on (default: the default branch; '' for none)")
    p.add_argument("--marker", default=DEFAULT_MARKER, help="comments containing this are the agent's own and are skipped")
    p.add_argument("--no-react", action="store_true", help="don't add a 👀 reaction to reported issues and comments")
    p.add_argument("--state-dir", default="~/.cache/coding-agents-skills/watch", help="where seen ids and ETags are kept")
    p.add_argument("--once", action="store_true", help="check once and exit")
    args = p.parse_args()

    if not args.repo:
        args.repo = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").stdout.strip()
        if not args.repo:
            sys.exit("no repo given and none found for the current directory")
    if args.branches is None:
        args.branches = gh("api", f"repos/{args.repo}", "--jq", ".default_branch").stdout.strip() or "main"
    args.branches = [b for b in args.branches.split(",") if b]

    watcher = Watcher(args)
    inbox = queue.Queue()
    pushed = not args.once and not args.no_webhook and start_webhook(args.repo, args.port, inbox)
    log(f"watching {args.repo} (commits on {','.join(args.branches) or 'no branch'}); "
        f"{'webhooks + ' if pushed else ''}conditional poll every {args.interval:g} s")

    while True:
        try:
            watcher.check()
        except Exception as e:  # keep watching through transient API or network errors
            log(f"check failed: {e!r}")
        if args.once:
            return
        # Webhook deliveries are reported as they arrive; the poll catches anything they missed.
        deadline = time.time() + args.interval
        while (left := deadline - time.time()) > 0:
            try:
                kind, payload = inbox.get(timeout=left)
            except queue.Empty:
                break
            try:
                watcher.event(kind, payload)
            except Exception as e:
                log(f"webhook {kind} failed: {e!r}")


if __name__ == "__main__":
    main()
