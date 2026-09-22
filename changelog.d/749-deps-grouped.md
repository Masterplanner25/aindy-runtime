### Changed — dependency bumps, grouped (#749)

Seven dependabot PRs (#741–#747) taken as one, for the reason #485 and #669 recorded: `strict:
true` branch protection means each individual merge forces a rebase of the others, and dependabot
resolves each package independently, so the set was merged together and verified to resolve
together (`pip install --dry-run` of the five Python bumps in one plan).

| Package | From | To |
|---|---|---|
| `anyio` | 4.15.0 | 4.15.1 |
| `joblib` | 1.5.3 | 1.6.0 |
| `pymongo` | 4.18.0 | 4.18.1 |
| `urllib3` | 2.7.0 | 2.8.0 |
| `uvicorn` | 0.52.3 | 0.53.0 |
| `cc` (native crate) | 1.4.5 | 1.4.7 |
| `react-router-dom` (platform UI) | 7.18.3 | 7.18.4 |

- **★ `joblib` 1.6.0 adds a transitive dependency, `cloudpickle`**, that nothing in the tree
  named. Pinned at 3.1.2 in both pin files — the tree pins transitives, and an unpinned one is
  the shape `NODUS-UPGRADE-1`'s third site exists for.
- `uvicorn` 0.53.0 is the one bump with server surface; the health / version / boot / routing /
  middleware / worker-pool / async-job suites ran green under it locally, the full sweep in CI.
