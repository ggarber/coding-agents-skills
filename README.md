# coding-agents-skills

Reusable skills for coding agents (Claude Code and others that read `SKILL.md` skills). Each skill lives in `skills/<name>/` with a `SKILL.md` and any scripts it needs, and stays project-agnostic: project specifics go in the project, not here.

| Skill | What it does |
| --- | --- |
| [`issue-monitor`](skills/issue-monitor/SKILL.md) | Watches a GitHub repo, turns each new issue into a tested, merged PR, and keeps answering comments. Events are pushed to the machine through GitHub webhooks, with cheap polling as a fallback. |

## Install

Clone once, then link the skills where your agent looks for them.

```bash
git clone https://github.com/ggarber/coding-agents-skills.git ~/projects/coding-agents-skills
```

**For all your projects** (Claude Code user skills):

```bash
mkdir -p ~/.claude/skills
ln -s ~/projects/coding-agents-skills/skills/issue-monitor ~/.claude/skills/issue-monitor
```

**For one project**, so everyone who clones it gets the skill, add this repo as a submodule and link the skill:

```bash
git submodule add https://github.com/ggarber/coding-agents-skills.git .claude/vendor/coding-agents-skills
mkdir -p .claude/skills
ln -s ../vendor/coding-agents-skills/skills/issue-monitor .claude/skills/issue-monitor
```

Update with `git pull` in the clone (or `git submodule update --remote`). The symlinks keep pointing at the latest version.

Other agents: point them at `skills/<name>/SKILL.md` (for example from your `AGENTS.md`).

## Project-specific rules

Skills read the project's `CLAUDE.md` / `AGENTS.md`. A skill can also read its own project file: `issue-monitor` reads `.claude/issue-monitor.md` if it exists. Put things there like which app vague issues usually mean, the checks to run, end-to-end recipes, and what must never be deployed without an explicit ask.

## issue-monitor

### Requirements

- [`gh`](https://cli.github.com/) logged in (`gh auth status`) with write access to the repo.
- Python 3.8+ (only the standard library).

### Push notifications instead of polling

GitHub can't reach a laptop directly, but the official [`gh webhook`](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/using-the-github-cli-to-forward-webhooks-for-testing) extension relays a repo's webhooks over an outbound connection. No public URL, tunnel or server is needed. When it's installed, the watcher uses it and reacts to new issues, comments, review comments and pushes within a second or two:

```bash
gh extension install cli/gh-webhook
gh auth refresh -s admin:repo_hook     # the forwarder creates a temporary repo webhook
```

Notes:
- You need admin rights on the repo (or org owner rights for org-wide hooks).
- GitHub allows one active forwarder per repo, so run one watcher per repo at a time.
- The forwarder is meant for development. If it drops, the watcher keeps polling and retries the forward every minute.

Without the extension, the watcher polls every 15 s with conditional requests (`If-None-Match` + ETag). An unchanged answer is a `304 Not Modified`, which [doesn't count against the rate limit](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests-if-appropriate), so even fast polling costs almost nothing. The webhook only works as a doorbell that triggers a check right away, so both paths report the same events, once each.

### Running the watcher by hand

```bash
python3 skills/issue-monitor/watch.py                  # repo of the current directory
python3 skills/issue-monitor/watch.py owner/repo --branches main,dev --interval 10
python3 skills/issue-monitor/watch.py --once --no-react  # one check, no 👀 reactions
python3 skills/issue-monitor/watch.py --help
```

It prints one line per event on stdout (`NEW_ISSUE`, `NEW_COMMENT`, `NEW_REVIEW_COMMENT`, `NEW_COMMIT`) and status on stderr. In Claude Code, the skill runs it under the Monitor tool, so each line reaches the agent as soon as it's printed. State is in `~/.cache/coding-agents-skills/watch/<owner>_<repo>.json`. Delete it to replay the last day (commits seen on the first run are recorded, not reported).

Comments containing `<!-- claude-issue-monitor -->` are skipped. The agent ends all of its comments with that marker, so it never answers itself when it posts from the owner's account. Change it with `--marker`.

## Adding a skill

1. `skills/<name>/SKILL.md` with `name` and `description` front matter. The description says when to use the skill.
2. Keep it generic: read project rules from the project (`CLAUDE.md`, `AGENTS.md`, `.claude/<name>.md`) instead of hard-coding them.
3. Scripts next to `SKILL.md`, standard library only where possible, with `--help`.
4. Add a row to the table above.

## License

MIT
