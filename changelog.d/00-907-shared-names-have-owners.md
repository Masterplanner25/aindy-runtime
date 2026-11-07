### Changed — webhooks, shared script names and shared workflow names now have owners (#907)

- **Webhooks:** a subscription now receives only the events of the user who created it. An admin's subscription still receives every tenant's, and so does one with no creator, such as a manifest's. It was wrong that delivery looked at the event type alone. **Check after upgrading:** a subscription created through a non-admin key stops receiving other users' events, and events with no user.
- **`POST /platform/nodus/upload`:** `overwrite` now replaces only your own script, otherwise 403 `script_not_owned`. An operator can still replace any. A script found only on disk now counts as existing, where it used to read as free after a restart. It has no recorded uploader, so only an operator can replace it.
- **`POST /platform/nodus/flow`:** registering a name that someone else holds now answers 403 `workflow_not_owned`. It always replaced the workflow before. A workflow with no creator, such as a plugin's or one registered before this release, can only be replaced by an operator. New registrations record their creator, and that survives a restart.

### Fixed — `/client/error` and `/client/vitals` attribute a report to its session (#907)

- These routes accept reports with no authentication, and they used the `user_id` in the body as the event's owner. The owner now comes from a valid session, and a report without one has no owner. Reports with a missing or bad token are still accepted, so errors during login are still captured.
