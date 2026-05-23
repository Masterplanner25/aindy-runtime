# Technical Debt

## SDK Extraction

Status: COMPLETE (2026-05-23)

`aindy-sdk` extracted to standalone repo:
https://github.com/Masterplanner25/aindy-sdk-

First green CI run:
https://github.com/Masterplanner25/aindy-sdk-/actions/runs/26343161733

`AINDY/sdk/` removed from `aindy-runtime` in this commit.

47 SDK tests pass in the standalone repo.

`aindy-runtime` packaging config confirmed - no explicit sdk include
required removal. `pyproject.toml` already used `include = ["AINDY*"]`,
so removing the directory was sufficient.
