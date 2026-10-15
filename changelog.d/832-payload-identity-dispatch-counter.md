### Added — `aindy_payload_identity_mismatch_total`: the dispatcher counts a payload `user_id` that differs from the caller (#832)

- FR-53 (#831) made the transports with an outside writer (MCP, Nodus `sys()`, `POST /platform/syscall`)
  refuse a payload `user_id` that is not the caller's. In-process dispatch is still unchecked: refusing
  there could break an unaudited system-context call made on behalf of another user. The dispatcher
  now **observes** a mismatch as a WARNING and `aindy_payload_identity_mismatch_total{syscall}`, and
  the call proceeds unchanged. Zero across a window of real traffic is the evidence for refusing it
  (`PAYLOAD-IDENTITY-DISPATCH-1`, DEC-100). Non-zero names the syscall to look at.
