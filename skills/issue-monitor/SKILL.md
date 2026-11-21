---
name: issue-monitor
description: Watch a GitHub repo's issues, comments, PR review comments and commits (webhooks pushed to the machine, with cheap polling as a fallback), take each new issue, implement it on a branch with local tests and proof, ship it through a PR with green CI, and keep answering comments (reopening done issues when needed). Use when asked to monitor, work through or babysit a repo's issues.
---

# Issue monitor

Turn GitHub issues into merged PRs, talking to the reporter only through issue comments.

## Project rules

Before the first issue, read the project's agent instructions: `CLAUDE.md` / `AGENTS.md` at the root and in the folders you touch, and `.claude/issue-monitor.md` if it exists. That file holds this project's specifics for this skill: which app an issue is likely about, checks to run, end-to-end test recipes, what must never be deployed or run without an explicit ask, commit and PR conventions. **Project rules win over this file.**

## Start

1. `gh auth status` must show an account with write access to the repo.
2. **Push events:** if `gh webhook --help` fails, install the extension: `gh extension install cli/gh-webhook`. If you aren't allowed to install it, carry on (the watcher polls instead) and tell the user once how to enable it. Creating the forwarding hook needs admin rights on the repo: when the watcher logs `webhook forward exited (... 403/404 ...)`, keep going on polling and tell the user once (an admin, or `gh auth refresh -s admin:repo_hook`, fixes it). `Hook already exists` right after a restart clears up by itself within a minute.
3. Arm a Monitor (max timeout, re-arm on every expiry) on the watcher, from the repo's directory:
   `python3 ${CLAUDE_SKILL_DIR}/watch.py`
   (pass `owner/repo` to watch another repo, `--branches main,dev` for the commit branches, `--interval N` for the poll period.)
   It prints one line per event and reacts 👀 to every issue and comment it reports:
   - `NEW_ISSUE #N [state] by USER: title`
   - `NEW_COMMENT #N [state] by USER (id ID): body…` (issues and PR conversations)
   - `NEW_REVIEW_COMMENT #N by USER (id ID) path:line: body…` (inline PR review comments)
   - `NEW_COMMIT branch sha by AUTHOR: subject` (the default branch unless `--branches` says otherwise)
   Status lines go to stderr. With the `gh webhook` extension, GitHub pushes events to the watcher (1–2 s latency); without it, or while the forward is down, it polls with conditional requests every 15 s. Stop it with SIGINT or SIGTERM so it removes its temporary repo hook. State (seen ids, ETags) lives in `~/.cache/coding-agents-skills/watch/`; delete the repo's file to replay the last day.
4. On first start, also run `gh issue list --state open` and take any open issue that has no comment from you yet.

Work on one issue at a time, oldest first. Events that arrive meanwhile wait their turn, except comments on the issue in progress, which you read right away. `NEW_COMMIT` lines are context (someone else changed the default branch: rebase your branch before pushing); your own merges show up there too and need nothing.

## Comments

The repo owner and the agent often post from the same account, so **every comment you write ends with the marker line `<!-- claude-issue-monitor -->`**. The watcher skips comments that contain it; without it you'd answer yourself forever. Post with `gh issue comment N --body-file <file>`.

Post exactly these, in order:
1. **Started:** one line, right when you take the issue.
2. **Plan:** what you'll change (files, approach), how you'll test it, and anything out of scope.
3. **Question** (only when blocked): specific questions, with the options you see and the one you'd pick. Then stop work on that issue and wait for a `NEW_COMMENT` on it. Move on to the next issue in the meantime.
4. **Done:** the PR link, what changed, and the proof (below).

React to others' comments with 👀 (the watcher does it) and add 👍 once you've acted on one (`gh api -X POST repos/OWNER/REPO/issues/comments/ID/reactions -f content=+1`; review comments use `pulls/comments/ID`).

## Implement

- **Pin down what the issue is about** before building. Vague words ("the web", "the app") can mean a different part of the repo than the obvious one. If the issue doesn't name the part, say which one you picked in the Plan comment. If the wording fits more than one, ask.
- Branch from a fresh default branch: `git checkout main && git pull && git checkout -b <type>/<N>-<slug>`.
- Clean code, minimal comments. Add a test only when it catches real regressions and is cheap to keep. No test scaffolding for its own sake.
- Test locally: the project's checks, unit tests where they exist, and its end-to-end recipe when the change touches that path. **Never deploy, spend money or touch shared environments unless the issue or a comment explicitly asks.**
- Update READMEs / design docs / `CLAUDE.md` with behaviour changes and with learnings that would have saved you time (project-specific ones go in `.claude/issue-monitor.md`).
- Follow the project's commit convention (Conventional Commits if it has none). Reference the issue: `Closes #N` in the PR body.
- **One issue, one commit:** each issue gets its own branch, PR and squashed commit. Never mix two issues in one commit or PR, even when one builds on the other: merge the first, then branch the second from the updated default branch. Changes that aren't for an issue (like skill or doc updates a comment asks for) get their own PR too.

## Proof

Every Done comment shows the work actually ran:
- A table of the test results: one row per check or test (what ran, result, key numbers such as timings or counts). When there's a before/after (e.g. a run on `main` vs the branch), put both in the table.
- Screenshots whenever there's anything to see (UI, a rendered page, a log view of the run), embedded as images in the comment, not only linked. Take them with the browser tools against a local dev server. Upload them by committing them to the PR branch under `.github/proof/<N>/`, or by attaching them to the PR description. Remove them before merging if they're bulky (> 1 MB).
- Link images by the merged path (`blob/main/.github/proof/<N>/…?raw=true`). Commit-SHA links on a branch you delete later can stop resolving.
- Command output of the checks and tests you ran (trimmed to the relevant part, in a collapsed `<details>` block), and the relevant log lines of any end-to-end run.
- Media for public pages (demo videos, images) must be licensed for reuse. Check the actual licence, not just the uploader's label, and credit it on the page.

## Ship

1. Push only once the work is finished and every local test has passed: no pushing half-done or untested work to let CI test it. `git push -u origin HEAD`, then `gh pr create` with: summary, `Closes #N`, the proof, and the attribution footer from the session.
2. Wait for CI: `gh pr checks --watch`. If it fails, fix, push and wait again. Never merge red.
3. `gh pr merge --squash --delete-branch` (unless the project merges differently), then `git checkout main && git pull`.
4. Post the Done comment. If the PR didn't close it, `gh issue close N --reason completed`.

## After done

Keep watching closed issues. A `NEW_COMMENT` on a closed issue that reports a problem or asks for more: `gh issue reopen N`, reply with a Started comment, and run the loop again. A comment that's just thanks gets a 👍 and nothing else. A `NEW_REVIEW_COMMENT` on an open PR of yours: address it on the same branch, push, reply in the review thread (with the marker), and wait for CI again.
