#!/bin/sh
# READ-ONLY DNS and reachability inspection. Changes nothing. Run it BEFORE preparing staging, on the Droplet or on any computer, and send the whole
# output back:   sh inspect_dns.sh [staging-host] [production-host]
# Defaults: preview.diamondfootballleague.com  diamondfootballleague.com
STAGING="${1:-preview.diamondfootballleague.com}"
PROD="${2:-diamondfootballleague.com}"
have() { command -v "$1" >/dev/null 2>&1; }
echo "== when/where"; date -u +%Y-%m-%dT%H:%M:%SZ; hostname
echo "== this machine's public address (only meaningful on the Droplet)"
if have curl; then
  curl -fsS -m 4 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null && echo "  (DigitalOcean metadata: public IPv4)" || echo "  (no DigitalOcean metadata: not on a Droplet, or blocked)"
  curl -fsS -m 4 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv6/address 2>/dev/null && echo "  (DigitalOcean metadata: public IPv6)"
fi
(ip -o -4 addr show scope global 2>/dev/null || true) | awk '{print "  local " $2 " " $4}'
for h in "$PROD" "www.$PROD" "$STAGING"; do
  echo "== DNS for $h"
  if have dig; then
    for t in A AAAA CNAME CAA; do printf "  %-6s" "$t"; dig +short +time=3 +tries=1 "$h" "$t" 2>&1 | tr '\n' ' '; echo; done
    echo "  NS (zone): $(dig +short +time=3 +tries=1 NS "$(echo "$PROD" | awk -F. '{print $(NF-1)"."$NF}')" 2>&1 | tr '\n' ' ')"
  elif have host; then
    host "$h" 2>&1 | head -6; host -t CAA "$h" 2>&1 | head -3
  elif have getent; then
    getent ahosts "$h" 2>&1 | head -6 || echo "  does not resolve"
  else
    python3 - "$h" <<'PY'
import socket, sys
try:
    print("  " + ", ".join(sorted({a[4][0] for a in socket.getaddrinfo(sys.argv[1], None)})))
except OSError as e:
    print("  does not resolve:", e)
PY
  fi
done
echo "== how to read this"
echo "  - the staging host must have an A record equal to the Droplet's public IPv4 (and, if it has an AAAA record, that must be the Droplet's IPv6)."
echo "  - a CAA record that does not list letsencrypt.org (or sectigo/zerossl for Caddy's fallbacks) will stop HTTPS certificates being issued."
echo "  - a CNAME on the staging host is fine if it ends at the Droplet's address."
echo "== what answers on this machine's ports 80 and 443 (Droplet only)"
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null || true) | awk 'NR==1 || /:80 |:443 /'
echo "== firewall on this machine (a DigitalOcean cloud firewall is not visible here: check Networking > Firewalls in the control panel for inbound 80 and 443)"
(ufw status 2>/dev/null || echo "  ufw not installed or not root") | head -12
echo "== what the live site answers today (headers only)"
for u in "https://$PROD/" "https://$STAGING/"; do echo "-- $u"; curl -sS -m 10 -I "$u" 2>&1 | head -12; done
