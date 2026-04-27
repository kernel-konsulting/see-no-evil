# Security policy

## Reporting a vulnerability

**Please do not file public issues for security vulnerabilities.**

Email: `security@kernel-konsulting.example` (placeholder until M1).

Include:

- A description of the vulnerability and its impact.
- Steps to reproduce, ideally with a minimal config.
- Affected versions / commits.
- Whether the issue has been disclosed elsewhere.

We will acknowledge within 72 hours and aim to ship a fix or mitigation within
30 days for high-severity issues.

## Scope

In scope:

- The container images published from this repository.
- The `services/*` source code in this repository.
- The default `config.example.yaml` and `deploy/compose/*` profiles.

Out of scope:

- Vulnerabilities in upstream projects (Blocky, Caddy, OPA, etc.) — please
  report those upstream. We're happy to coordinate.
- Cert-pinned mobile apps that bypass MITM inspection. This is documented as
  a known limitation in `docs/threat-model.md`, not a vulnerability in this
  project.

## What we promise

- We will never ship telemetry that exfiltrates inspected user content. This is
  a hard architectural rule (see `docs/architecture.md` §"Egress posture").
- All releases are signed. SBOMs are published with each release starting at M1.
