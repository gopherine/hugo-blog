---
title: "Lesson 1: Git Beyond Basics — Rebase, bisect, worktrees"
author: Atharva Pandey
keywords:
  - git
  - git rebase
  - git bisect
  - git worktrees
  - version control
  - engineering practices
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 1
date: '2024-05-07T00:00:00.000Z'
---

Most engineers use maybe 15% of Git. `add`, `commit`, `push`, `pull`, `branch`, `merge`, and `status` covers daily work. That's fine until you need to find which commit introduced a regression across 300 commits, or you need to untangle a messy history before merging, or you want to work on three features simultaneously without context-switching overhead. The commands I'm covering here don't come up every day. When they do, they save hours.

## How It Works

**Rebase: Rewriting History**

`git rebase` moves or replays commits onto a new base. The most common use: keeping a feature branch up to date with main without creating merge commits.

```
Before rebase:
A - B - C          (main)
    \
     D - E - F     (feature)

git checkout feature && git rebase main

After rebase:
A - B - C          (main)
         \
          D' - E' - F'  (feature, rebased onto C)
```

The commits D, E, F are replayed on top of C. Their SHAs change (hence D', E', F') but their changes are preserved. The result is a linear history — the feature looks like it was built on top of the current main.

Interactive rebase (`git rebase -i HEAD~5`) lets you edit, reorder, squash, or drop commits before pushing:

```bash
# Interactively edit the last 5 commits
git rebase -i HEAD~5

# In the editor:
pick 3a7b2c4 Add order validation
pick 8f2d1e9 fix typo in validation
pick 2c9a8b7 wip
squash 5e1f3d2 more wip
pick 7a4c6b1 Add tests for order validation
```

Change `pick` to:
- `squash` (or `s`): Combine with the previous commit
- `fixup` (or `f`): Combine, discard this commit's message
- `reword` (or `r`): Edit the commit message
- `drop` (or `d`): Delete the commit entirely
- `edit` (or `e`): Pause rebase to amend this commit

The golden rule of rebase: **never rebase commits that have been pushed to a shared branch**. Rebasing rewrites history. If someone else has pulled those commits, your force-push will conflict with their local history.

**Bisect: Binary Search for Regression**

`git bisect` uses binary search to find which commit introduced a bug. If you have 300 commits and a regression, bisect narrows it down in ~8 steps (log2(300) ≈ 8).

```bash
# Start a bisect session
git bisect start

# Tell git the current state is bad
git bisect bad HEAD

# Tell git a known good state (e.g., a release tag)
git bisect good v1.2.0

# Git checks out the middle commit
# ... run your test, verify ...
# If bad:
git bisect bad
# If good:
git bisect good
# Git checks out the next middle commit...
# Repeat until git says: "abc123 is the first bad commit"

# When done, return to original HEAD
git bisect reset
```

Bisect can be automated if you have a test that reproduces the bug:

```bash
git bisect start HEAD v1.2.0
git bisect run ./test_regression.sh
# Git runs the script at each candidate commit.
# Script exit 0 = good, exit non-zero = bad.
# Git finds the first bad commit automatically.
git bisect reset
```

This turns a multi-hour debugging session into a 5-minute command.

**Worktrees: Multiple Working Directories**

A Git worktree lets you check out multiple branches simultaneously in different directories — without cloning the repo twice.

```bash
# Check out a branch in a new directory alongside your main checkout
git worktree add ../hotfix hotfix/critical-bug

# Now you have:
# /projects/myapp/        ← your main checkout (feature branch)
# /projects/hotfix/       ← separate checkout (hotfix branch)

# Work in the hotfix directory
cd ../hotfix
# make changes, commit, push

# Remove the worktree when done
git worktree remove ../hotfix
```

Worktrees share the same `.git` directory and object store — no data duplication. You can run tests in one worktree while editing in another. Each worktree has its own working directory and index, so they're fully independent.

I use worktrees to:
- Fix a hotfix branch while a long-running test suite runs in the main checkout
- Review a colleague's PR without disrupting my current work context
- Run two branches side-by-side to compare behavior

## Why It Matters

These three tools solve distinct common problems:

- **Rebase** gives you a clean history that's easy to code-review and easy to revert if needed. A squashed, rebased PR tells a story. A merge-commit-heavy branch tells a chronicle.
- **Bisect** turns regression investigation from "read every commit message and hope" into a deterministic algorithm. It's the most time-efficient debugging tool in Git's toolbox for "which commit broke this."
- **Worktrees** eliminate the context-switching overhead of stashing, switching branches, and re-stashing. Hotfixes don't interrupt in-progress feature work.

## Production Example

A full rebase workflow for a team that uses GitHub PRs:

```bash
# Create feature branch
git checkout -b feature/order-validation main

# Do work... multiple commits during development
git commit -m "wip: basic order validation structure"
git commit -m "wip: add price validation"
git commit -m "fix: off-by-one in quantity check"
git commit -m "wip: add tests"
git commit -m "tests passing"

# Before opening a PR: clean up the history
git rebase -i main
# In the editor — squash all into one clean commit:
# pick abc1234 wip: basic order validation structure
# squash def5678 wip: add price validation
# squash ghi9012 fix: off-by-one in quantity check
# squash jkl3456 wip: add tests
# squash mno7890 tests passing
# → Becomes one commit: "Add order validation with tests"

# Bring up to date with main before opening PR
git fetch origin
git rebase origin/main

# Force push the cleaned branch (OK because this is your feature branch, not shared)
git push --force-with-lease origin feature/order-validation
# --force-with-lease is safer than --force: fails if someone else pushed
```

Automating bisect for a known regression:

```bash
#!/usr/bin/env bash
# test_regression.sh — exits 0 if test passes, 1 if it fails

set -e
go build ./... || exit 1

# Run the specific test that's regressing
go test ./internal/orders -run TestOrderTotal -count=1
# go test exits 0 on success, non-zero on failure — exactly what bisect needs
```

```bash
git bisect start HEAD v1.4.0
git bisect run ./test_regression.sh
# Output: "abc123def is the first bad commit"
# git show abc123def — see exactly what changed
git bisect reset
```

Worktree for parallel work:

```bash
# You're deep in a feature. Production incident happens.
# Stash isn't enough — you need to run tests in both.

git worktree add /tmp/incident-fix hotfix/payment-timeout
cd /tmp/incident-fix

# Fix the incident
vim internal/payments/client.go
go test ./internal/payments/...
git commit -m "Fix payment client timeout configuration"
git push origin hotfix/payment-timeout

# PR merged, now clean up
cd /projects/myapp  # back to your feature work, untouched
git worktree remove /tmp/incident-fix

# Check worktrees
git worktree list
# /projects/myapp       a1b2c3d [feature/order-validation]
# (clean — incident worktree removed)
```

## The Tradeoffs

**Rebase vs merge history**: Rebase gives you a linear, clean history. Merge preserves the exact history of when branches diverged and converged. Both have value. For feature work, I prefer rebase. For release branches and merge commits that represent significant milestones, I keep the merge commit. Never rebase a branch after another person's commits have been added to it.

**Interactive rebase risks**: Squashing and reordering commits can introduce conflicts if the commits touch the same files in incompatible ways. Always rebase against a local branch (not the remote), and keep a backup tag before a complex rebase: `git tag backup-before-rebase`.

**Bisect limitations**: Bisect only works if the regression is deterministic — every run at the bad commit reproduces it. Flaky tests, timing-dependent bugs, and environment-specific issues make bisect unreliable. If the bisect script is sometimes wrong, bisect is sometimes wrong.

**Worktree branch restrictions**: You can't check out the same branch in two worktrees simultaneously. If you need to compare two states of the same branch, you need to create a separate branch at the snapshot you want.

**Reflog as safety net**: Before any scary Git operation, remember: `git reflog` shows every state your HEAD has been in. If you mess up a rebase, `git reset --hard HEAD@{5}` (or whatever reflog entry you want) gets you back. The reflog is your safety net.

## Key Takeaway

Git's power comes from features most engineers never use. Interactive rebase turns a chaotic development history into a clean, reviewable narrative. Bisect turns regression hunting from guesswork into a binary search algorithm. Worktrees let you work on multiple branches simultaneously without losing context. These tools don't replace the fundamentals — they build on them. Master one per quarter until they're automatic. The compounding return on your productivity is significant.

---

**Next:** [Lesson 2: Code Review That Works — What to look for](/post/fundamentals/eng-code-review/)
