Behavioral guidelines to reduce common LLM coding mistakes. Merge these with project-specific instructions as needed.

**Tradeoff:** These guidelines favor caution over speed. For trivial tasks, use judgment.

This file keeps development collaboration and reasoning guidance only.
User-facing AI collect scope restrictions live in
`app/web/policies/ai_collect_scope.json`.

## Order of Precedence

When instructions conflict, follow them in this order:

1. Task-specific human instructions
2. General behavioral guidance in this file

## 1. Think Before Coding

**Do not assume. Do not hide confusion. Surface tradeoffs.**

Before implementing:
- State assumptions explicitly.
- If multiple interpretations exist, present them instead of choosing silently.
- If a simpler approach is enough, say so.
- If an important detail is unclear, stop and ask.

## 2. Simplicity First

**Use the minimum change that solves the request. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No flexibility or configurability that was not requested.
- No defensive handling for impossible scenarios.
- If you wrote 200 lines and 50 would do, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what the task requires. Clean up only the mess you create.**

When editing existing code:
- Do not improve adjacent code, comments, or formatting unless the task requires it.
- Do not refactor working code just because you would structure it differently.
- Match the local style.
- If you notice unrelated dead code, mention it. Do not delete it.

When your changes create orphans:
- Remove imports, variables, or functions made unused by your own edits.
- Do not remove pre-existing dead code unless asked.

Test every changed line against the request. If it does not trace back directly, it probably should not be in the diff.

## 4. Goal-Driven Execution

**Define success criteria, then loop until verified.**

Turn vague requests into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step work, state a short plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria support independent execution. Weak criteria like "make it work" usually require clarification.

## Success Signal

These guidelines are working if diffs get smaller, rewrites get rarer, and clarifying questions happen before implementation instead of after mistakes.
