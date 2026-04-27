# OIDC sign-in

By default the admin UI authenticates against a local Argon2id password
(seeded by the first-run wizard). For shared deployments you can layer
OpenID Connect on top — sign-in via Google Workspace, GitHub, Authentik,
Keycloak, or anything else that speaks OIDC.

OIDC is **additive**: the local password still works as a break-glass
account. To require OIDC exclusively, leave the local password unset or
rotate it to a randomly generated value after first-time setup.

## Quick start

In `config.yaml`:

```yaml
auth:
  builtin:
    enabled: true              # keep as a break-glass account
  oidc:
    enabled: true
    issuer: https://accounts.google.com
    client_id: xxxxxxxxxxxx.apps.googleusercontent.com
    client_secret: GOCSPX-xxxxxxxx
    redirect_url: https://seenoevil.lan/v1/auth/oidc/callback
    allowed_emails:
      - parent1@example.com
      - parent2@example.com
    scopes: [openid, email, profile]   # default
```

Restart the API. The login screen will show a "Sign in with SSO" button
that calls `GET /v1/auth/oidc/start` and redirects the browser to the IdP.

## How it works

1. Browser hits `/v1/auth/oidc/start`. The server picks up the configured
   `redirect_url`, generates a PKCE pair (S256) plus a `state` token, and
   stashes both in the `settings` table for 10 minutes.
2. Browser is redirected to the IdP's authorize endpoint.
3. IdP redirects back to `/v1/auth/oidc/callback?code=...&state=...`.
4. The server validates `state`, exchanges `code` for an access token via
   the token endpoint, then calls `userinfo` to fetch `email`.
5. If `email` is in `allowed_emails`, a session cookie is issued and the
   browser is redirected to `/`. Otherwise the response is HTTP 403.

## Why no JWKS validation?

The token's signature is enforced implicitly: only the IdP can mint a
working access token, and we only trust the email returned from the IdP's
`userinfo` endpoint over TLS. This keeps the OIDC client tiny (~200 lines,
no JOSE dependency). If you operate in an environment where this trade-off
is unacceptable, run an OIDC-aware reverse proxy (oauth2-proxy,
authelia, etc.) in front of Caddy and forward the verified email as a
trusted header.

## Allow-list semantics

- Empty `allowed_emails` => any successful sign-in is admitted. Use this
  only behind a tightly scoped IdP (e.g. a Google Workspace domain).
- Non-empty list => exact case-insensitive match required. Wildcards are
  not supported.

## Reverting

Set `auth.oidc.enabled: false` and restart. Browser sessions established
via OIDC remain valid until they expire (default 7 days) — to force a
global sign-out, restart the API with a new `auth.builtin` JWT secret.
