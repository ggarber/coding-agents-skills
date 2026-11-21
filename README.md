# coding-agents-skills

Reusable skills for coding agents (Claude Code and others that read `SKILL.md` skills). Each skill lives in `skills/<name>/` with a `SKILL.md` and any scripts it needs, and stays project-agnostic: project specifics go in the project, not here.

| Skill | What it does |
| --- | --- |
| [`issue-monitor`](skills/issue-monitor/SKILL.md) | Watches a GitHub repo, turns each new issue into a tested, merged PR, and keeps answering comments. Events are pushed to the machine through GitHub webhooks, with cheap polling as a fallback. |

## Install

Claude Code loads skills from two places:

| Where | Path | Who gets it |
| --- | --- | --- |
| The whole computer | `~/.claude/skills/<name>/` | You, in every project |
| One project | `<project>/.claude/skills/<name>/` | Everyone who works on that project |

A skill is just its folder (`SKILL.md` plus scripts), so installing means putting that folder, or a link to it, in one of those paths. Start a new Claude Code session afterwards. The skill shows up in `/skills`, and you can call it by name (`/issue-monitor`) or just ask for it ("monitor the issues").

### On the whole computer

Clone once and link the skills, so a `git pull` updates them everywhere:

```bash
git clone https://github.com/ggarber/coding-agents-skills.git ~/projects/coding-agents-skills
mkdir -p ~/.claude/skills

# one skill
ln -s ~/projects/coding-agents-skills/skills/issue-monitor ~/.claude/skills/issue-monitor

# or every skill in the repo (re-run after pulling new skills)
for d in ~/projects/coding-agents-skills/skills/*/; do ln -sfn "$d" ~/.claude/skills/"$(basename "$d")"; done
```

Update: `git -C ~/projects/coding-agents-skills pull`.

### In one project, shared through its repo

Add this repo as a git submodule and link the skill into `.claude/skills`. The link and the submodule are committed, so anyone who clones the project gets the skill at the version you pinned:

```bash
cd <project>
git submodule add https://github.com/ggarber/coding-agents-skills.git .claude/vendor/coding-agents-skills
mkdir -p .claude/skills
ln -s ../vendor/coding-agents-skills/skills/issue-monitor .claude/skills/issue-monitor
git add .gitmodules .claude && git commit -m "chore: add issue-monitor skill"
```

- Cloning the project: `git clone --recurse-submodules <url>`, or `git submodule update --init` in an existing clone. Without it the link points at an empty folder and the skill doesn't load.
- Updating to the latest skills: `git submodule update --remote .claude/vendor/coding-agents-skills`, then commit the new submodule commit.
- Add the project's own rules for the skill in `.claude/<name>.md` (see below).

### In one project, as a plain copy

No submodule, no link: copy the folder in and commit it. It won't update by itself; copy it again to update.

```bash
mkdir -p <project>/.claude/skills
cp -R ~/projects/coding-agents-skills/skills/issue-monitor <project>/.claude/skills/
```

### Check and remove

```bash
ls -l ~/.claude/skills/ <project>/.claude/skills/    # what's installed and where links point
python3 ~/.claude/skills/issue-monitor/watch.py --help # the skill's scripts run from there
rm ~/.claude/skills/issue-monitor                      # remove a linked skill (the clone stays)
```

Install a skill in one place only. With the same skill both on the computer and in a project, it's unclear which copy the agent uses.

### Other agents

Agents that don't load `SKILL.md` skills can still follow them: point them at `skills/<name>/SKILL.md` from the project's `AGENTS.md` (or equivalent), for example "To work GitHub issues, follow `.claude/vendor/coding-agents-skills/skills/issue-monitor/SKILL.md`."

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
```

The skill installs it itself when the agent is allowed to.

Notes:
- The forwarder creates a temporary webhook on the repo, so you need admin rights on it. The default `gh auth login` token is enough for your own repos; if creating the hook fails with 403/404, run `gh auth refresh -s admin:repo_hook`.
- GitHub allows one active forwarder per repo, so run one watcher per repo at a time.
- Stop the watcher with Ctrl-C / SIGTERM so it removes the hook. After a hard kill, the next start logs `Hook already exists` and retries until GitHub drops the old one.
- The forwarder is meant for development. If it drops, the watcher keeps polling and retries the forward.
- Measured: a new issue or comment reaches the watcher in about 2 s, with the poll interval set to 5 minutes.

Without the extension, the watcher polls every 15 s with conditional requests (`If-None-Match` + ETag). An unchanged answer is a `304 Not Modified`, which [doesn't count against the rate limit](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests-if-appropriate), so even fast polling costs almost nothing. Webhook deliveries are reported straight from their payload, and the poll catches anything they missed. Both paths share one record of what was reported, so each event is printed once.

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
