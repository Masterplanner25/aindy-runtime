### Added — `aindy-runtime jobs settle-stale-units` closes units the stale cleanup stranded (#886)

- Before 2.29.2, the stale-log cleanup failed a job row and left its execution unit `pending` forever.
  One stack had 2,387. They hold no admission slot, but they skew unit counts by status.
- The command moves only a `job_log` unit still `pending` whose row is `failed` with the cleanup's own
  message, to `failed` (DEC-107). The job row is not changed. It reports only unless given `--yes`, and
  nothing runs on upgrade.

```bash
aindy-runtime jobs settle-stale-units        # reports only
aindy-runtime jobs settle-stale-units --yes  # settles
```
