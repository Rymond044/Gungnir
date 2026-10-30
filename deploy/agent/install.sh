#!/bin/sh
# Installs or upgrades the agent as a systemd service. Run as root from this
# directory, after building: (cd ../../agent && cargo build --release).
# Usage: install.sh [TOKEN_FILE]
# TOKEN_FILE holds the token printed by `gungnir-core host add`; it may be
# omitted on an upgrade, when /etc/gungnir/agent.token is already in place.
set -eu

token_file=${1:-}
if [ -z "$token_file" ] && [ ! -e /etc/gungnir/agent.token ]; then
    echo "usage: install.sh TOKEN_FILE (no token installed yet)" >&2
    exit 1
fi
binary=../../agent/target/release/gungnir-agent
[ -x "$binary" ] || { echo "build first: $binary not found" >&2; exit 1; }

install -m 0755 "$binary" /usr/local/bin/gungnir-agent
install -d -m 0755 /etc/gungnir
[ -e /etc/gungnir/agent.toml ] || install -m 0644 agent.toml /etc/gungnir/agent.toml
[ -z "$token_file" ] || install -m 0600 -o root -g root "$token_file" /etc/gungnir/agent.token
install -m 0644 gungnir-agent.service /etc/systemd/system/gungnir-agent.service

systemctl daemon-reload
systemctl enable --now gungnir-agent.service
systemctl restart gungnir-agent.service
systemctl --no-pager status gungnir-agent.service
