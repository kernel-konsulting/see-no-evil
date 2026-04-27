"""see-no-evil LAN scanner.

Periodically scans the configured CIDR with nmap, posts discovered devices
(MAC + IP + hostname + vendor) to the API at ``/v1/devices/discover``.
"""
