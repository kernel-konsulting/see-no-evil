# Updater

The **only** container that is permitted to make outbound HTTP requests other
than DNS / NTP. Pulls and verifies:

- DNS blocklists (per `dns.blocklists`).
- Model weights on first start (per `updates.models`); SHA-256 verified.
- IEEE OUI database (per `updates.oui`).

Runs on a cron schedule. All other containers should consider the contents of
`/data/{models,lists}` as read-only data they did not fetch themselves.

## Why a separate container?

- Egress firewall rules can be tight on every other container.
- The model / list registries change without code changes.
- Failures in fetching are isolated and easy to retry.
