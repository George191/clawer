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
