---
title: "Runtime GitHub Settings Checklist"
last_verified: "2026-05-17"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime GitHub Settings Checklist

This document lists the manual GitHub UI settings that should be configured for
`aindy-runtime`.

These settings are not stored in git and must be recreated in GitHub after the
repo split.

## Branch Baseline

Recommended baseline:

- default branch: `main`
- protect branch: `main`

The current runtime workflows are designed around `push` and `pull_request`
events on `main`.

## Recommended Branch Protection

In GitHub:

- Settings -> Branches -> Add branch protection rule
- branch name pattern: `main`

Recommended protections:

- require a pull request before merging: enabled
- required approvals: `1`
- dismiss stale pull request approvals when new commits are pushed: enabled
- require conversation resolution before merging: enabled
- require status checks to pass before merging: enabled
- restrict direct pushes to `main`: enabled

Recommended required status checks:

- `Runtime Lint`
- `Runtime Docs Validation`
- `Runtime Contracts`
- `Runtime Package Build`

Do **not** require:

- `Runtime Release Staging`

That workflow is manual-only (`workflow_dispatch`) and is not intended to block
normal merges.

## Merge Policy

Recommended:

- allow squash merge: enabled
- allow merge commit: disabled
- allow rebase merge: optional, team preference

Reasoning:

- squash merge keeps runtime history cleaner for packaging and release review
- merge commits are not needed for the current runtime repo workflow model

## Optional Stronger Settings

These are reasonable if the team wants a stricter governance posture:

- require linear history: enabled
- require signed commits: enabled if the team already uses signed commits
- allow auto-merge: enabled
- automatically delete head branches: enabled
- require approval of the most recent reviewable push: enabled

Only enable these if they match how the team actually works; they are not
required by the current runtime workflow design.

## Actions / Workflow Settings

Recommended GitHub repo settings:

- GitHub Actions: enabled
- workflow permissions: read repository contents unless a future workflow needs
  write access
- fork pull request approval policy: set according to org policy

## First-Run Note

GitHub only lets branch protection require status checks that have already run
at least once on the repository.

Recommended sequence:

1. push `.github/workflows/runtime-ci.yml` to `main`
2. let `Runtime CI` run once successfully
3. configure the required status checks listed above
