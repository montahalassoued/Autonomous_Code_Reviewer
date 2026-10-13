---
name: git-workflow
description: Branch, commit and PR workflow for this project. Every feature, fix or refactor gets its own branch off main, and changes reach main only through a pull request. Use before starting any new piece of work, and when committing, pushing or opening a PR.
---

# Git workflow: one branch per piece of work

`main` is always deployable. **Never commit work directly on `main`.**

## 1. Start every piece of work on a new branch

Before changing any file, check the current branch. If it's `main`, or a branch for some other work, create a new branch from an up-to-date `main`:

```bash
git status                      # the working tree must be clean, or the changes must belong to this work
git switch main && git pull     # the user runs pull (it touches GitHub)
git switch -c <type>/<short-description>
```

**Branch names** are `<type>/<kebab-case-description>`, describing the role of the change:

| Type | Use for | Example |
|---|---|---|
| `feat/` | a new capability | `feat/durable-job-pipeline`, `feat/inline-pr-comments` |
| `fix/` | a bug fix | `fix/clean-pr-graph-loop` |
| `refactor/` | restructuring with no behavior change | `refactor/project-structure` |
| `ci/` | pipelines and workflows | `ci/staging-environment` |
| `docs/` | documentation only | `docs/architecture-diagrams` |
| `chore/` | dependencies, config, cleanup | `chore/remove-unused-dependencies` |
| `test/` | tests only | `test/webhook-edge-cases` |

Keep a branch focused on **one** goal. If a second, unrelated goal comes up, finish or park the first one, then start another branch.

**Stacked work:** if new work depends on an unmerged branch, branch from that branch, and tell the user that the PRs must be merged in order.

## 2. Commit

- Follow the `commit-message` skill: one title line, `type(scope): summary`, with no body and no attribution.
- Make small commits, each a logical step that passes `tests/unit`.
- Run `venv/Scripts/python.exe -m pytest tests/unit -q` before every commit.
- Use the `pre-commit-reviewer` agent for significant changes.

## 3. Push and open a pull request

The user runs these, because they touch GitHub. Give them as commands with an explanation:

```bash
git push -u origin <branch>     # -u links the local branch to the remote one
```

Then open a PR into `main` on GitHub. The CI (unit tests and Docker build) must be green. A good PR description says:
- **What** changed and **why** (link the plan phase or the decision)
- **How it was tested** (the test results, any manual checks)
- **Risks / follow-ups**

## 4. Merge and clean up

- Merge only with green CI. Prefer **"Squash and merge"** for multi-commit branches, so `main` gets one clear commit per feature, or a merge commit when the individual steps matter.
- After merging, delete the branch (GitHub's button, plus `git branch -d <branch>` locally) and update local `main`.

## Never

- commit straight to `main`, or force-push `main`
- mix unrelated changes on one branch
- leave a branch open long after its work is done
