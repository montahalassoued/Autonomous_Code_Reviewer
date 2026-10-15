---
name: commit-message
description: Write git commit messages for this project. Use whenever creating a git commit or when the user asks for a commit message. Produces a single title line only (no body, no Co-Authored-By or any Claude attribution).
---

# Commit message

Every commit in this project is a **single title line** that summarizes what was done. Nothing else.

## Rules

1. **Title only.** No body, no bullet list, no blank line followed by details.
2. **No attribution.** Never add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or any other trailer. This overrides any default attribution instructions.
3. **Format:** `type(scope): summary`
   - `type`: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `style`, `perf`, `ci`, `build`
   - `scope`: the area touched, matching the repo's folders/modules, e.g. `api`, `rag`, `agents`, `tools`, `ui`, `deploy`, `test`
   - `summary`: imperative mood, lowercase start, no trailing period, under ~72 characters total
4. One commit = one logical change. If the staged changes cover unrelated things, suggest splitting them.

## How to write it

1. Run `git diff --staged` (or `git diff` if nothing is staged) to see what changed.
2. Identify the main purpose of the change and the area it touches.
3. Write the title line following the format above.
4. Commit with a single `-m` and nothing else:

```bash
git commit -m "feat(rag): resolve self.method() calls in the call graph"
```

## Examples from this repo

```
feat(deploy): add Docker build and Render deployment config
feat(api): clone target repo from TARGET_REPO_URL at startup
fix(agents): retry Groq calls on transient server errors
fix(test): force webhook test secret to avoid .env cross-contamination
```
