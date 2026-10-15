---
name: git-workflow
description: Commit and push workflow for this project. All work is committed directly on main, with no feature branches or pull requests unless the user asks. Use before starting any new piece of work, and when committing or pushing.
---

# Git workflow: commit on `main`

The user chose to work **directly on `main`**, with no feature branches and no pull requests. Create a branch or a PR only when the user asks for one.

## 1. Before starting

```bash
git status                      # the working tree must be clean, or the changes must belong to this work
git switch main                 # if you are not on main
```

If `main` may be behind GitHub, give the user `git pull` to run (it touches GitHub).

## 2. Commit

- Follow the `commit-message` skill: one title line, `type(scope): summary`, with no body and no attribution.
- Make small commits, each a logical step: one commit per concern (code, build/infra, docs, Claude config), so a step can be reverted alone.
- Run `venv/Scripts/python.exe -m pytest tests/unit -q` before **every** commit. There is no PR to catch a red test before `main`, so this is the safety net.
- Use the `pre-commit-reviewer` agent for significant changes.
- After each commit, tell the user the commit title and its files.

## 3. Push

The user runs it, because it touches GitHub. Give it as a command with an explanation:

```bash
git push                        # sends the new commits on main to GitHub
```

The push to `main` starts the CI: offline tests, Docker build, and the Azure deploy when the `ACR_NAME` variable exists. Tell the user to check the Actions tab, and fix a red run with a new commit right away.

## Never

- force-push `main` or rewrite pushed commits (`reset --hard`, `rebase` of pushed history)
- commit with red unit tests
- mix unrelated changes in one commit
