# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — scanner
# -----------------------------------------------------------------------------
# Periodic LAN sweep using nmap. Reports discovered devices (MAC + IP +
# hostname + vendor) to the API at ``POST /v1/devices/discover``.
#
# Needs CAP_NET_RAW + CAP_NET_ADMIN to run nmap -sn (ARP ping). Granted at the
# compose level, not in the image.
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends nmap iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/scanner/pyproject.toml ./
COPY services/scanner/src ./src
RUN pip install --no-cache-dir -e .

COPY services/scanner/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Container runs as root so nmap can use raw sockets. The scanner process
# itself does no other privileged work.
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /tmp/scanner_ready"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
