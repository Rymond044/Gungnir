#!/bin/sh
# Installs or upgrades the agent and the actuator as systemd services. Run as
# root from this directory, after building: (cd ../../agent && cargo build --release).
# Usage: install.sh [TOKEN_FILE]
# TOKEN_FILE holds the token printed by `gungnir-server host add`; it may be
# omitted on an upgrade, when /etc/gungnir/agent.token is already in place.
set -eu

token_file=${1:-}
if [ -z "$token_file" ] && [ ! -e /etc/gungnir/agent.token ]; then
    echo "usage: install.sh TOKEN_FILE (no token installed yet)" >&2
    exit 1
fi
for name in gungnir-agent gungnir-actuator; do
    binary=../../agent/target/release/$name
    [ -x "$binary" ] || { echo "build first: $binary not found" >&2; exit 1; }
    install -m 0755 "$binary" /usr/local/bin/$name
done

# The group of the actuator's socket (ADR 0062).
install -D -m 0644 systemd/gungnir.sysusers /etc/sysusers.d/gungnir.conf
systemd-sysusers /etc/sysusers.d/gungnir.conf

install -d -m 0755 /etc/gungnir
[ -e /etc/gungnir/agent.toml ] || install -m 0644 agent.toml /etc/gungnir/agent.toml
[ -e /etc/gungnir/actuator.toml ] || install -m 0644 actuator.toml /etc/gungnir/actuator.toml
# The key was `core_url` before the core was renamed the server (ADR 0050).
sed -i 's/^core_url\b/server_url/' /etc/gungnir/agent.toml
[ -z "$token_file" ] || install -m 0600 -o root -g root "$token_file" /etc/gungnir/agent.token
install -m 0644 gungnir-agent.service /etc/systemd/system/gungnir-agent.service
install -m 0644 gungnir-actuator.service /etc/systemd/system/gungnir-actuator.service

# I/O accounting in every control group (ADR 0036). The service managers read
# these at start, so they take full effect after the next boot.
install -D -m 0644 systemd/io-accounting.conf /etc/systemd/system.conf.d/gungnir-io.conf
install -D -m 0644 systemd/io-accounting.conf /etc/systemd/user.conf.d/gungnir-io.conf
install -D -m 0644 systemd/user-io-delegation.conf /etc/systemd/system/user@.service.d/gungnir-io.conf

systemctl daemon-reload
systemctl enable --now gungnir-actuator.service gungnir-agent.service
systemctl restart gungnir-actuator.service gungnir-agent.service
systemctl --no-pager status gungnir-actuator.service gungnir-agent.service
echo "I/O accounting for every control group is configured; reboot for it to take effect."
