# Tutorials

> **Relocated into `aindy-runtime` (2026-06-27).** Runtime surfaces used by
> these tutorials were re-validated against the current runtime on relocation.
> A few signatures drifted since authoring and are flagged inline with
> **Runtime note** callouts in the individual tutorials:
> - WAIT/RESUME is the Nodus `event.wait(event_type)` **builtin**, not a
>   `sys.v1.event.wait` syscall (Tutorial 2).
> - `sys.v1.flow.run` takes `initial_state`, not `input` (Tutorial 3).
> - The trace endpoint path param is `{trace_id}` (Tutorial 2); the delete
>   schedule path param is `{job_id}` (Tutorial 3).
>
> The `AINDY.sdk.aindy_sdk` client used below ships in the separately published
> **aindy-sdk** package, not in this runtime repo.

Three tutorials. Each takes under 10 minutes and ends with something running.

| # | Tutorial | What you'll see |
|---|----------|-----------------|
| 1 | [Memory-Driven Task Analyzer](01-memory-driven-workflow.md) | Memory → execution → insight loop |
| 2 | [Event-Driven Automation](02-event-driven-automation.md) | Flow pauses, waits for a signal, resumes |
| 3 | [Scheduled Intelligence](03-scheduled-execution.md) | System runs without you |

**Prerequisites for all three:**

> **Import path note.** The SDK is the separate `aindy-sdk` distribution, imported as
> `aindy_sdk`. Examples in these tutorials previously used `AINDY.sdk.aindy_sdk`, a path from
> before the SDK was split out of the runtime package — that module does not exist, so every
> Python example failed at its import line. Corrected 2026-08-05.

```bash
# Server running (runtime repo entry point)
aindy-runtime serve          # or: uvicorn AINDY.runtime_only:app --reload

# SDK available — published to PyPI; the local editable install predates that
pip install aindy-sdk

# API key in your environment
export AINDY_API_KEY="aindy_your_key"
export AINDY_BASE_URL="http://localhost:8000"
```
