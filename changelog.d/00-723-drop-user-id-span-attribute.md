### Removed — `user.id` on `syscall.*` spans (#723; DEC-036, deprecated with a date in 2.20.0)

- The `syscall.*` OTel span no longer carries `user.id`. It was emitted beside the semconv key
  `enduser.id` for one release (2.20.0) as announced; `enduser.id` carries the same value. A
  dashboard or alert filtering on `user.id` for syscall spans must move to `enduser.id` before
  upgrading.
