# Remote access (VPN)

see-no-evil deliberately does not expose the admin UI to the internet. To
manage the pod when you're away from the LAN, layer a VPN on top of it.
Two ready-to-go profiles ship in `deploy/compose/docker-compose.yml`:

| Profile | Tool | Best for |
|---|---|---|
| `vpn-tailscale` | [Tailscale](https://tailscale.com) | One-click mesh; no port forwarding. Free tier covers ≤100 devices. |
| `vpn-wg` | [wg-easy](https://github.com/wg-easy/wg-easy) | Self-hosted WireGuard; one UDP port forwarded on your router. |

Both are sidecars on the same pod — they don't replace your home firewall.

## Tailscale (recommended for most users)

1. Create a tagged auth key at <https://login.tailscale.com/admin/settings/keys>
   (recommend `--ephemeral=false --reusable=false --preauthorized`).
2. Drop the key into `deploy/compose/secrets/tailscale_authkey.txt`.
3. Bring it up:

   ```bash
   docker compose --profile core --profile vpn-tailscale up -d
   ```

4. From a phone or laptop on the tailnet, hit `https://seenoevil` (or
   whatever you set in `pod.hostname`). Tailscale resolves the MagicDNS
   name; Caddy serves the UI.

The container runs with `network_mode: host` so it can join the kernel's
WireGuard interface and announce itself.

## wg-easy

Use this when you'd rather not depend on a SaaS coordinator.

1. Open UDP port `51820` on your router and forward to the pod.
2. Set an admin password and bring it up:

   ```bash
   WG_HOST=home.example.com \
   PASSWORD=changeme \
   docker compose --profile core --profile vpn-wg up -d
   ```

3. Browse to `http://<pod-ip>:51821`, create peers, scan QR codes onto
   phones.

`wg-easy` exposes the WireGuard UI on `:51821` — keep this LAN-only or
reverse-proxy it behind Caddy with auth.

## DNS over the VPN

Both profiles allow remote clients to resolve LAN hostnames *and* benefit
from the Blocky filter. Point the VPN's "DNS server" setting at the pod's
internal IP. With Tailscale this is configured under "DNS" in the admin
console.

## Nothing else changes

Filtering, classification, and every API call still happens on the pod.
The VPN just gives you a private route into it.
