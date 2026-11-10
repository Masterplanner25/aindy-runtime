# Platform UI (SPA under `platform/`, served at `/platform`)

Moved from the root `CLAUDE.md`, which keeps the rules that touch `AINDY/` (`_SPAStaticFiles`) and releases
(the SPA ships prebuilt in the wheel).

- `PlatformGuard` (`platform/src/PlatformApp.tsx`): `/login` stays outside the guard; redirect via
  `<Navigate to="/login" replace />` (never `window.location`); authenticated-but-not-admin renders
  `<NotAdmin />` (a `<Navigate>` there loops). `VITE_APP_BASE_URL` is removed — do not reintroduce
  it as a redirect target. `VITE_API_BASE_URL` defaults to `""` (relative); vite `server.proxy`
  covers local dev.
- ui-kit: source `C:\dev\aindy-ui-kit\src\`, npm `@aindy/ui-kit`; the installed copy is a compiled
  bundle. Loop: build ui-kit → copy `dist/*` into `platform/node_modules/@aindy/ui-kit/dist/` →
  `npm run build` in `platform/` → restart `api`. `bootIdentity`, `loginUser`, `registerUser` must
  all `.then(unwrapEnvelope)` or the post-login redirect misfires.
