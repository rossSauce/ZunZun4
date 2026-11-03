#!/bin/bash
# Server setup for zunzunsite3. Run from the project directory:   sudo bash deploy/install.sh
#
#   1. nginx: drop requests for unknown host names (the default server used to publish
#      all of /var/www/html), serve /temp/ graphs directly, block dot-files such as .git
#   2. a 2 GB swap file, if the machine has no swap
#   3. gunicorn as a systemd service (zunzunsite3) in place of the root
#      "manage.py runserver" started from /etc/rc.local
#
# Safe to re-run: each step checks whether it has already been done. Existing files are
# backed up with a .bak-<timestamp> suffix next to the original before being replaced.

set -euo pipefail

PROJECT=/var/www/html/zunzunsite3
DEPLOY="$PROJECT/deploy"
STAMP=$(date +%Y%m%d-%H%M%S)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo bash deploy/install.sh" >&2
    exit 1
fi

backup() { # backup <file>
    if [ -e "$1" ]; then cp -p "$1" "$1.bak-$STAMP"; echo "  backed up $1 -> $1.bak-$STAMP"; fi
}

echo "== 1. nginx"
for site in default findcurves.com; do
    target=/etc/nginx/sites-available/$site
    if ! cmp -s "$DEPLOY/nginx/$site" "$target"; then
        backup "$target"
        install -m 644 "$DEPLOY/nginx/$site" "$target"
        echo "  installed $target"
    fi
done
if nginx -t; then
    systemctl reload nginx
    echo "  nginx reloaded"
else
    echo "  nginx -t failed; restoring the backups and stopping" >&2
    for site in default findcurves.com; do
        [ -e "/etc/nginx/sites-available/$site.bak-$STAMP" ] && cp -p "/etc/nginx/sites-available/$site.bak-$STAMP" "/etc/nginx/sites-available/$site"
    done
    exit 1
fi

echo "== 2. swap"
if [ "$(awk 'NR>1' /proc/swaps | wc -l)" -gt 0 ]; then
    echo "  swap already enabled:"; cat /proc/swaps
else
    if [ ! -e /swapfile ]; then
        fallocate -l 2G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
    fi
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || { backup /etc/fstab; echo '/swapfile none swap sw 0 0' >> /etc/fstab; }
    echo "  2 GB swap enabled at /swapfile"
fi

echo "== 3. gunicorn service"
if ! command -v gunicorn >/dev/null; then
    apt-get install -y gunicorn
fi
# the service runs as rossmoffett, who has pyeq3 in ~/.local; check it imports before switching over
if ! sudo -u rossmoffett -H python3 -c "import pyeq3, django, gunicorn" ; then
    echo "  pyeq3, django or gunicorn does not import for rossmoffett; not switching over" >&2
    exit 1
fi

# stop the old root runserver: rc.local runs runZunZun.sh, which never returns
if grep -q '^[^#]*runZunZun.sh' /etc/rc.local 2>/dev/null; then
    backup /etc/rc.local
    sed -i 's|^\([^#]*runZunZun.sh.*\)$|# replaced by zunzunsite3.service (deploy/install.sh): \1|' /etc/rc.local
    echo "  disabled runZunZun.sh in /etc/rc.local"
fi
systemctl stop rc-local.service || true

# anything else still on port 8000 (e.g. a "manage.py runserver" in a terminal) would block gunicorn;
# on a re-run the service itself holds the port, which is fine (it is restarted below)
if ! systemctl is-active -q zunzunsite3.service && ss -ltnH 'sport = :8000' | grep -q .; then
    echo "  port 8000 is still in use:" >&2
    ss -ltnpH 'sport = :8000' >&2
    echo "  stop that process (development servers should use port 8001), then re-run this script" >&2
    exit 1
fi

install -m 644 "$DEPLOY/systemd/zunzunsite3.service" /etc/systemd/system/zunzunsite3.service
systemctl daemon-reload
systemctl enable zunzunsite3.service
systemctl restart zunzunsite3.service

echo -n "  waiting for gunicorn"
for i in $(seq 1 60); do
    if curl -s -o /dev/null -m 10 http://127.0.0.1:8000/; then echo; break; fi
    echo -n "."; sleep 2
done
code=$(curl -s -o /dev/null -m 60 -w '%{http_code}' -H 'Host: findcurves.com' http://127.0.0.1:8000/ || true)
echo "  home page via gunicorn: HTTP $code"
if [ "$code" != "200" ]; then
    echo "  not healthy; see: journalctl -u zunzunsite3 -n 50" >&2
    exit 1
fi
echo "== 4. weekly security check"
# cron runs a root-owned copy: the copy in the project directory can be edited by the
# account the site runs as, and root must never run a file that account can change
install -o root -g root -m 755 "$DEPLOY/security.py" /usr/local/sbin/zunzun-security
SECURITY_EMAIL="${SECURITY_EMAIL:-rossmoffett@findcurves.com}"
cat > /etc/cron.d/zunzunsite3-security <<EOF
# Weekly security check (installed by deploy/install.sh). Emails $SECURITY_EMAIL if it
# finds any warnings or alerts; the last report is in /var/log/zunzunsite3-security.log
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 3 * * 0 root /usr/local/sbin/zunzun-security --no-color --email $SECURITY_EMAIL --email-on problems > /var/log/zunzunsite3-security.log 2>&1
EOF
chmod 644 /etc/cron.d/zunzunsite3-security
echo "  installed /usr/local/sbin/zunzun-security and /etc/cron.d/zunzunsite3-security (Sundays 03:30)"
CONF=/etc/zunzunsite3/security_email.conf
if [ ! -e "$CONF" ]; then
    install -d -m 750 /etc/zunzunsite3
    install -o root -g root -m 600 "$DEPLOY/security_email.conf.example" "$CONF"
    echo "  created $CONF from the example: put the real SMTP login in it before the first run" >&2
elif grep -q 'xxxxxxxxxxxxxxxx' "$CONF"; then
    echo "  $CONF still has the example password: edit it, or the weekly report cannot be emailed" >&2
fi

echo "Done."
