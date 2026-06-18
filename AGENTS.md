Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

# AI/Agent Code Modification Rules

These rules apply to all AI assistants, coding agents, automation agents, and scripts that modify this repository.

## Hard Scope

Agents may only make the following repository changes:

1. Add new template files under `templates/`.
2. Add new adapter files under `app/adapters/`.

All other modifications are forbidden unless the human maintainer gives explicit written permission for that specific change.

## Allowed

- Create a new template file in `templates/`.
- Create a new adapter file in `app/adapters/`.
- Create this policy file or update this policy file only when the human maintainer explicitly requests it.

## Forbidden

Agents must not:

- Modify existing files in `templates/`.
- Delete files from `templates/`.
- Rename or move files in `templates/`.
- Modify existing files in `app/adapters/`.
- Delete files from `app/adapters/`.
- Rename or move files in `app/adapters/`.
- Modify, delete, rename, or move any file outside `templates/` and `app/adapters/`.
- Reformat unrelated files.
- Update dependency files, lockfiles, environment files, generated files, or configuration files without explicit written permission.
- Run commands that rewrite, clean, reset, or discard existing worktree changes.

## Required Workflow

Before making changes, agents must:

1. Check the current worktree state.
2. Treat all pre-existing changes as human-owned.
3. Avoid touching any human-owned changes.

After making changes, agents must verify that the diff contains only:

- Newly added files in `templates/`, and/or
- Newly added files in `app/adapters/`, and/or
- This policy file when explicitly requested.

If a requested task requires modifying or deleting an existing file, the agent must stop and ask the human maintainer for explicit permission before proceeding.

