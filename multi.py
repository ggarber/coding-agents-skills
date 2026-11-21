#!/usr/bin/env python3
"""Launch one Claude Code session per project folder, each running the issue-monitor skill.

Folders come from the command line, or else from config.yaml (next to this script, or --config):

    folders:
      - ~/projects/app-one
      - ~/projects/app-two
    permission_mode: auto        # optional: unattended sessions need one that doesn't prompt
    claude_args: [--model, opus] # optional: extra arguments for every session

Before launching, the skill is copied into each project's .claude/skills/issue-monitor (and
listed in .git/info/exclude so it stays out of the project's git status). A project that already
has the skill as a link or committed folder keeps its own. Each session runs in its own tmux
session (when tmux is installed) or Terminal window (macOS).

    multi.py                         # folders from config.yaml
    multi.py ~/projects/a ~/projects/b
    multi.py --dry-run               # print what would happen, change nothing
    multi.py --copy-only             # copy the skill into the folders, don't launch
    multi.py --stop                  # stop the tmux sessions (Terminal windows: close them)
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL = "issue-monitor"
DEFAULT_PROMPT = f"/{SKILL}"
TMUX_PREFIX = "issues-"


def parse_yaml(text):
    """PyYAML when installed, otherwise the subset used by config.yaml: `key: value`,
    `key: [a, b]` and `key:` followed by `- item` lines."""
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    def scalar(v):
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            return v[1:-1]
        return v

    data, key = {}, None
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        item = re.match(r"\s*-\s+(.*)$", line)
        if item and key:
            data.setdefault(key, []).append(scalar(item.group(1)))
            continue
        kv = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not kv:
            sys.exit(f"config: can't parse line: {raw!r} (install PyYAML for full YAML)")
        key, value = kv.group(1), kv.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [scalar(v) for v in value[1:-1].split(",") if v.strip()]
        elif value:
            data[key] = scalar(value)
    return data


def load_config(path, required):
    if not os.path.exists(path):
        if required:
            sys.exit(f"no folders given and no config at {path} (see config.example.yaml)")
        return {}
    with open(path) as f:
        return parse_yaml(f.read())


def git(folder, *args):
    return subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True)


def install_skill(folder, dry_run):
    """Copy the skill into the project. Returns a one-line note on what happened."""
    rel = os.path.join(".claude", "skills", SKILL)
    dest = os.path.join(folder, rel)
    if os.path.islink(dest):
        return f"skill: {rel} is a link to {os.readlink(dest)}, kept"
    if git(folder, "ls-files", rel).stdout.strip():
        return f"skill: {rel} is committed in the project, kept"
    if dry_run:
        return f"skill: would copy to {rel}"
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(os.path.join(ROOT, "skills", SKILL), dest,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Keep the copy out of the project's git status without touching its .gitignore.
    exclude = git(folder, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    if exclude:
        exclude = os.path.join(folder, exclude) if not os.path.isabs(exclude) else exclude
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        lines = open(exclude).read().splitlines() if os.path.exists(exclude) else []
        entry = f"/{rel}/"
        if entry not in lines:
            with open(exclude, "a") as f:
                f.write(("\n" if lines and lines[-1] else "") + entry + "\n")
    return f"skill: copied to {rel}"


def session_name(folder):
    return TMUX_PREFIX + re.sub(r"[^\w-]", "_", os.path.basename(folder.rstrip("/")))


def command(folder, args, cfg):
    cmd = ["claude", "--name", f"issues: {os.path.basename(folder)}"]
    mode = args.permission_mode or cfg.get("permission_mode")
    if mode:
        cmd += ["--permission-mode", mode]
    cmd += [str(a) for a in cfg.get("claude_args") or []]
    cmd += [args.prompt or cfg.get("prompt") or DEFAULT_PROMPT]
    return cmd


def launch(folder, cmd, launcher, dry_run, note):
    line = f"cd {shlex.quote(folder)} && {shlex.join(cmd)}"
    print(f"{folder}\n  {note}")
    if launcher == "tmux":
        name = session_name(folder)
        full = ["tmux", "new-session", "-d", "-s", name, "-c", folder, shlex.join(cmd)]
        print(f"  tmux attach -t {name}\n  {line}")
        if not dry_run:
            if subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode == 0:
                print(f"  already running, skipped (multi.py --stop to restart)")
                return
            subprocess.run(full, check=True)
    else:
        print(f"  new Terminal window\n  {line}")
        if not dry_run:
            script = f'tell application "Terminal" to do script {applescript_string(line)}'
            subprocess.run(["osascript", "-e", script], check=True, stdout=subprocess.DEVNULL)


def applescript_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def stop():
    if not shutil.which("tmux"):
        sys.exit("no tmux: close the Terminal windows instead (Ctrl-C, then /exit)")
    out = subprocess.run(["tmux", "list-sessions", "-F", "#S"], capture_output=True, text=True).stdout
    names = [n for n in out.split() if n.startswith(TMUX_PREFIX)]
    for name in names:
        # Ctrl-C twice exits Claude Code; its watcher then removes its webhook on the way out.
        subprocess.run(["tmux", "send-keys", "-t", name, "C-c"])
        subprocess.run(["tmux", "send-keys", "-t", name, "C-c"])
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
        print(f"stopped {name}")
    if not names:
        print("nothing running")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folders", nargs="*", help="project folders (default: the config's folders)")
    p.add_argument("--config", default=os.path.join(ROOT, "config.yaml"), help="config file (default: config.yaml next to this script)")
    p.add_argument("--launcher", choices=["tmux", "terminal"], help="default: tmux if installed, else Terminal (macOS)")
    p.add_argument("--permission-mode", help="claude --permission-mode for every session (overrides the config)")
    p.add_argument("--prompt", help=f"first message of every session (default: {DEFAULT_PROMPT})")
    p.add_argument("--dry-run", action="store_true", help="print what would happen without copying or launching")
    p.add_argument("--copy-only", action="store_true", help="copy the skill into the folders without launching")
    p.add_argument("--stop", action="store_true", help="stop the tmux sessions this script started")
    args = p.parse_args()

    if args.stop:
        return stop()

    cfg = load_config(args.config, required=not args.folders)
    folders = args.folders or cfg.get("folders") or []
    if not folders:
        sys.exit(f"no folders in {args.config}")

    launcher = args.launcher or ("tmux" if shutil.which("tmux") else "terminal")
    if launcher == "terminal" and sys.platform != "darwin":
        sys.exit("no tmux and not macOS: install tmux")
    if not shutil.which("claude") or not shutil.which("gh"):
        sys.exit("needs the claude and gh CLIs on PATH")

    seen = set()
    for folder in folders:
        folder = os.path.abspath(os.path.expanduser(str(folder)))
        if folder in seen:
            continue
        seen.add(folder)
        if git(folder, "rev-parse", "--show-toplevel").stdout.strip() != os.path.realpath(folder):
            print(f"{folder}\n  skipped: not the root of a git repository", file=sys.stderr)
            continue
        note = install_skill(folder, args.dry_run)
        if args.copy_only:
            print(f"{folder}\n  {note}")
            continue
        launch(folder, command(folder, args, cfg), launcher, args.dry_run, note)


if __name__ == "__main__":
    main()
