# UI (admin web UI)

React + Vite + TypeScript. Served as static files by Caddy in production; runs
under Vite dev server in development.

## Status

Implemented admin shell with cookie-backed login, protected routes, dashboard
summary, device and profile management, quarantine review, and audit log views.
The audit log supports clearing entries, live refresh, thumbnail previews with
placeholders for unsupported media, and expandable wrapped URLs for inspection.
First-run install wizard and WebAuthn enrollment remain deferred.

## Responsibilities

- Login.
- Profile editor.
- Device list + assign-to-profile.
- Live audit log viewer.
- Quarantine review queue.
