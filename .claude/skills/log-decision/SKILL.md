---
name: log-decision
description: Record an architecture or design decision in DECISIONS.md. Use when a real choice was made between alternatives (a library, a pattern, a workaround for an API constraint, a bug-driven redesign), or when the user says "log this decision" / "add to DECISIONS".
---

# Log a decision

Decisions go in the **root `DECISIONS.md`**, the detailed log. `docs/DECISIONS.md` is a short one-line summary list. If the decision is important, also add a single bullet there in its `- X over Y: reason` style.

## When to log

Log it when someone would later ask "why didn't they just ...?":
- choosing one tool or library over another
- a design forced by a framework constraint or a bug (like the `join_agents` barrier)
- a deliberate trade-off (cost, speed, accuracy, simplicity)

Don't log routine implementation details.

## Format

Append to the end of `DECISIONS.md`, with one blank line before the heading:

```markdown
## Why <choice> over <alternative>

<Context: what problem we hit or what we needed - 1-3 sentences.>
<Decision: what we chose and how it works - 1-3 sentences.>
<Alternatives: what else was considered and why it lost.>
<Trade-off: what we gave up, and why that's acceptable here.>
<Optional: "Validated in test/test_x.py." or the file it lives in.>
```

Write it as one or two plain paragraphs like the existing entries, not as labeled bullets. The headings in angle brackets above are only a checklist of what to cover.

## Rules

- The heading starts with `## Why ...` (or `## <Library> API constraint: ...` for framework gotchas), matching the existing entries.
- Be concrete: name the files, the error message, and the numbers (e.g. "5x the synthesizer LLM calls").
- Keep it short, around 4-10 lines.
- Make sure every entry has its `## ` heading. The last existing entry ("Why tree-sitter ...") is missing one, so fix it when you're next editing the file.
