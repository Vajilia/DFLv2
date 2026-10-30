#!/bin/sh
# READ-ONLY inspection of the web server, to be run on the Droplet before anything is deployed. It changes nothing. Paste its output back to the
# project so the deployment plan can be checked against what is really there (docs/WEBSITE_DEPLOYMENT_PLAN.md, section 1).
echo "== host"; hostname; uname -sr; . /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
echo "== resources"; nproc; free -m; df -h / /var /opt 2>/dev/null
echo "== services"; systemctl list-units --type=service --no-pager 2>/dev/null | grep -Ei 'caddy|dfl' ; systemctl list-timers --no-pager 2>/dev/null | grep -i dfl
echo "== listening"; (ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | head -20
echo "== caddy"; command -v caddy && caddy version; ls -la /etc/caddy 2>/dev/null; echo "-- Caddyfile (secrets masked)"; sed -E 's/(basic_auth[^{]*\{)/\1 [masked]/; s/\$2[aby]\$[^ ]+/[hash masked]/g' /etc/caddy/Caddyfile 2>/dev/null
echo "== directories"; ls -la /opt/dfl /var/www/dfl /srv/dflv2 2>/dev/null | head -60
echo "== units"; ls -la /etc/systemd/system/ 2>/dev/null | grep -i dfl; for u in /etc/systemd/system/dfl-*; do [ -f "$u" ] && { echo "-- $u"; cat "$u"; }; done
echo "== current load"; uptime; ps -eo pid,pcpu,pmem,rss,comm --sort=-rss | head -8
