### Fixed — the three tutorials did not work, and had not since they were written (docs/tutorials-correctness)

Every call in `docs/tutorials/` was checked against the SDK source, the syscall registry, the
routes and the installed Nodus 5.13 interpreter. Findings, all corrected: every tutorial's first
write used `/memory/demo/…`, which puts `demo` in the **tenant** slot of
`/memory/{tenant}/{namespace}/{type}/{id}` and raises `TENANT_VIOLATION`; every Nodus script
failed to parse (`if`/`while` need parentheses since nodus 5); `event.wait()`, `emit()` and
`sys.v1.event.wait` do not exist; `memory.tree` has no `flat` key; `flow.run` returns
`{"flow_result": …}` not the flow's keys; `POST /platform/flows` needs `platform.admin` and a
node registry that is empty on a bare runtime; the schedule route's fields were all wrong.
Tutorial 2's model was wrong at the root — a guest script re-runs from the top on resume, it
does not continue — and its approval event could not have woken the run.

### Fixed — `NODUS_DEVELOPER_GUIDE.md` examples and its WAIT section

Seven examples had unparenthesised conditions that no longer parse; the pin section said
4.2.0. §4 claimed the event bus delivers the payload into `nodus_received_events` — it does
not; only the resume route does. Rewritten to describe both paths.

### Added — `WAIT-PAYLOAD-PATH-1` filed; `docs/tutorials/` joins `Runtime Docs Validation`

The bus-resume path carries no payload and usually does not even match the wait (it keys on
the run's `trace_id`; the emit carries its own), and local vs cross-instance correlation rules
disagree. Filed as a design question with the minimum fix stated. The tutorials folder now has
frontmatter and is checked by CI; it was the one docs folder outside the check.
