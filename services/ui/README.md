# UI (admin web UI)

React + Vite + TypeScript. Served as static files by Caddy in production; runs
under Vite dev server in development.

**M0:** Dockerfile stub only. No source yet.

## Responsibilities

- Login / WebAuthn enrollment.
- Profile editor.
- Device list + assign-to-profile.
- Live audit log viewer.
- Install wizard (first-run): set admin password, choose DNS upstream, generate
  or import MITM CA, print device-onboarding instructions.
