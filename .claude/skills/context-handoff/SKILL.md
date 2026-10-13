---
name: context-handoff
description: Summarize the current session into .claude/handoff.md so the user can start a fresh context window without losing progress. Use when the status line shows context over 40%, or when the user says "summarize and start a new context", "handoff", or "context is full".
---

# Context handoff

When the context window gets full, quality drops and old details crowd out new ones. This skill saves everything important into one file. The user then runs `/clear` and starts the new session with "read .claude/handoff.md".

Claude can't run `/clear`, `/compact` or `/context` itself, because those are built-in commands the user types.

## Step 1: Write `.claude/handoff.md`

Overwrite the file with the structure below. Keep it **short and concrete**, under ~150 lines: paths, line numbers, commit hashes and decisions, not narration. Leave out anything that's already in CLAUDE.md, the skills, or the git history. Point to those instead.

```markdown
# Session handoff - <YYYY-MM-DD>

## Goal
<What the user is trying to achieve overall, in 1-3 lines.>

## Done this session
- <commit hash> <commit title> - <one line on why>
- <uncommitted changes, if any: file - what changed>

## Current state
<What works now, and what's in progress. Mention any half-finished edit explicitly.>

## Key findings to remember
- <important facts discovered, with file:line, that aren't obvious from the code>

## Decisions made (and by whom)
- <decision> - <reason> - <user-approved / pending>

## Next steps (in order)
1. <next concrete task, and which agent or skill to use>
2. ...

## Open questions for the user
- <question, with the context needed to answer it>
```

## Step 2: Check before finishing

- Run `git status` and `git log --oneline -10` so the "Done" list and the uncommitted changes are accurate.
- Make sure no secrets or API keys end up in the file.
- `.claude/handoff.md` is gitignored. It's a personal working note and isn't committed.

## Step 3: Tell the user

Reply with:
1. A link to `.claude/handoff.md` and a 3-5 line summary of what it contains.
2. These instructions:
   - Type `/clear` to start a fresh context window.
   - Then send: **"Read .claude/handoff.md and continue from the next steps."**
   - Another option is `/compact`, which keeps the same session but replaces the history with an automatic summary. The handoff file is more reliable, because the user controls what's kept.
3. If unsure of the current usage, the user can type `/context` to see exactly how the context window is used.
