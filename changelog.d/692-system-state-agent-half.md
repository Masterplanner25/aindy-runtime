### Fixed — the system-wide state snapshot never counted agent runs (`SYSTEM-STATE-TENANT-1`, #692)

- `compute_current_state` (`AINDY/platform_layer/system_state_service.py`) reported
  `active_runs` and `avg_execution_time` without any agent run, on every call since 2.0.0.
  Its two agent inputs were fetched through `sys.v1.agent.count_runs` /
  `sys.v1.agent.list_recent_durations` with `user_id=None`; the dispatcher refuses an empty
  tenant before the handler runs, and the service read the error envelope as `0` / `[]`.
  Both numbers feed `system_load` and `health_status`, so a deployment's health could read
  calmer than it was.
- Why the obvious fix was wrong: a supplied tenant would not have helped — both syscalls
  scope to ONE user by construction, and this snapshot is not per-user. `AgentRun` is now
  read directly across every tenant, the way `FlowRun` always was in the same function.
- Consumer-visible: the next snapshot after upgrade includes agent runs. Nothing else in the
  response shape changes. Both syscalls stay registered (`count_runs` has an app consumer;
  `list_recent_durations` now has none — kept, removal is a separate decision).
- Found by the app team's `SYSCALL-SILENT-ERRORS-1` (their #365) from
  `aindy_syscall_outcome_total`; not filed as an FR. Test drives the real function on a seeded
  session and pins that the snapshot dispatches no syscall at all.
