#!/bin/sh
# M0 stub entrypoint. Replaced in M1.
set -eu
echo "see-no-evil scanner: M0 stub starting"
touch /tmp/ready
# Sleep forever so the container stays up and `docker compose` is happy.
exec sleep infinity
