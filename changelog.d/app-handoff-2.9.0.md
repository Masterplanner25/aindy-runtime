### Added — app-team handoff for 2.9.0

- `docs/runtime/APP_HANDOFF_v2.9.0.md`. Upgrading **from 2.8.0** needs no schema step; §1 is the
  one required consumer change — the envelope's `status` gained `partial` and `unknown`, so branch
  on `!= "success"` and never on `== "error"`.
- **It says explicitly that a deployment on 2.7.0 or earlier still owes 2.8.0's schema step.**
  "2.9.0 needs no schema step" is true of the *release* and false for a deployment that skipped
  one, and that gap is exactly the shape of the 2.1.0 handoff whose "nothing to backfill" was true
  about data and read to a deployer as "nothing to do".
