### Changed — dependency bumps, grouped (#669)

Eight dependabot PRs (#660–#667) taken as one, for the reason #485 recorded: `strict: true`
branch protection means each individual merge forces a rebase of the other seven, and
dependabot resolves each package independently, so the set was merged together and verified to
resolve together (`pip install --dry-run --no-cache-dir -r AINDY/requirements.txt`).

| Package | From | To |
|---|---|---|
| `click` | 8.4.2 | 8.5.0 |
| `jiter` | 0.11.1 | 0.16.0 |
| `psycopg2` | 2.9.12 | 2.9.13 |
| `tqdm` | 4.70.0 | 4.70.1 |
| `ruff` (dev) | 0.16.6 | 0.16.7 |
| `uuid` (Rust) | 1.26.0 | 1.26.1 |
| `react` / `react-dom` / `@types/react` / `@types/react-dom` (platform SPA) | 19.x | 19.3.0 |
| `vite` (platform SPA, dev) | 8.2.2 | 8.3.0 |

Every Python pin moved in **both** `pyproject.toml` and `AINDY/requirements.txt` (`ruff` lives
only in the latter). No consumer-visible behaviour change is expected from any of these; `jiter`
is the widest jump (five minors) and is a transitive of the LLM client SDKs, exercised by the
metered-seam and cassette tests. **Gotcha recorded:** a stale local pip HTTP cache reported
`click==8.5.0` as non-existent — `--no-cache-dir` before concluding a pin is wrong.
