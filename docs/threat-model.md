# see-no-evil — Threat model (M0 skeleton)

> This is a **skeleton**. Each section will be expanded in subsequent
> milestones. The point at M0 is to be honest about what this tool can and
> cannot do, so users don't develop a false sense of security.

## Who is this for?

- Parents who want to reduce (not eliminate) NSFW exposure on home networks.
- Schools and small non-profits that need an auditable, self-hosted filter.
- Hobbyists who want to learn how MITM filtering actually works.

## Who is this NOT for?

- Anyone who needs DLP-grade exfiltration prevention.
- Anyone protecting against a sophisticated insider with shell access to the
  filtered devices.
- Anyone who needs guaranteed inspection of cert-pinned apps.

## Assets we protect

| Asset | Why it matters |
|---|---|
| Inspected user content (URLs, bodies, image bytes, audit decisions) | Privacy. **Must never leave the box.** |
| MITM CA private key | Whoever controls it can impersonate any HTTPS site to filtered devices. |
| Admin credentials | Whoever holds them can disable filtering. |
| Audit log integrity | Tampering hides the fact that filtering was bypassed or disabled. |
| Configuration | Tampering downgrades thresholds or whitelists malicious sites. |

## Adversaries we consider

| Adversary | Capability | In scope? |
|---|---|---|
| Curious kid on filtered device | Browser, basic settings | **Yes** — we should make bypass non-trivial. |
| Tech-savvy teen | Can install software, change DNS, sideload apps | **Partial** — see "known bypasses" below. |
| Malicious site operator | Crafts content to evade classifiers | **Yes** — adversarial robustness of models is a continuous concern. |
| Network attacker on the LAN | ARP spoofing, rogue DHCP | **Partial** — we recommend, but do not require, network segmentation. |
| Compromised upstream (model repo, blocklist) | Supply-chain attack | **Yes** — checksums + pinned revisions; see `SECURITY.md`. |
| State-level adversary | Pretty much anything | **No.** Don't use this for that. |

## Known bypasses (and our mitigations)

| Bypass | Mitigation |
|---|---|
| Cert-pinned mobile apps (Instagram, TikTok, YouTube app) | Document clearly. These devices fall back to DNS-only filtering, or are placed in a "kid VLAN" that has no internet without the proxy. |
| DoH / DoT directly to a public resolver from the device | Block the canonical-name `use-application-dns.net`; block known DoH endpoints by IP at the firewall (optional `dns.block_doh_ips` flag). |
| QUIC / HTTP/3 | The proxy strips `Alt-Svc` headers and the firewall blocks UDP/443 outbound from filtered VLANs. |
| User installs their own VPN | Outside our control on the device; recommend network segmentation that blocks unknown UDP outbound. |
| Image classifier evaded by adversarial perturbations | Classifier is one of several layers. Text + URL + DNS still fire. |
| Removing the see-no-evil CA from device trust store | Then HTTPS to MITM'd domains breaks loudly — they get a cert error, not silent bypass. |
| Spoofing another device's identity to inherit its profile (`X-Device-Mac` / `X-Forwarded-For` headers) | The proxy ignores client-supplied identity headers entirely and attributes by the TCP source IP; the API resolves devices IP-first (scanner-learned IP→MAC) or by device id. A filtered device can still *change its own IP* (DHCP) to escape attribution — same exposure as any IP-based filter. |
| Crafting requests straight at the API to bypass `/v1/decide` | The API is on the pod's internal network; `/v1/decide`, `/v1/runtime`, `/v1/quota/heartbeat` require the proxy bearer token, `/v1/devices/discover` requires the scanner token, and Caddy refuses those routes at the edge. |

## Security architecture decisions

- **Default-deny egress for inspection containers.** Image / text / video
  classifiers and the API have no internet access. Only the `updater` does, on
  a schedule.
- **MITM CA private key encrypted at rest** with AES-256-GCM (key derived via
  PBKDF2-HMAC-SHA256) when `PROXY_CA_PASSPHRASE` is set; the proxy refuses to
  start without the passphrase if the key was written encrypted.
- **Audit log integrity.** Every `audit_decisions` row is signed with an
  HMAC-SHA256 keyed off a per-install secret; `/v1/audit` reports
  `signature_valid` so tampering (e.g. scrubbing history in the SQLite file)
  is detectable. Rows predating the signature column report `null`.
- **Pinned model revisions.** Model weights and the tokenizer are pinned by
  SHA-256 in the updater catalogue; the updater refuses mismatched files.
- **No outbound telemetry.** Ever. Hard architectural rule.
- **Secrets via files / env**, not baked into images (mountable as Docker /
  K8s secrets).

## Things we explicitly punt on (M0)

- Formal STRIDE or LINDDUN walkthrough — will be added by M2.
- Hardware attestation / measured boot — out of scope.
- Multi-tenancy isolation between profiles at the OS level — single-tenant pod;
  profiles are an application-layer construct.

## Reporting issues

See [`SECURITY.md`](../SECURITY.md).
