#!/bin/bash
# The reference scenario С-1 (slice S9): a program leaks memory slowly in
# its own scope, the growth check sees it after three rising hours (ADR
# 0058), the host runs short of memory and a victim waits; the incident
# should name memory_leak with the scope as culprit and propose memory.high.
# The script confirms that proposal as actions.sh does and stops the leak
# once the action is verified, or after `max_h` hours.
#
# Usage: leak.sh HOST [MiB_PER_MIN]   (default 30: about 4 h to exhaust 8 GiB)
set -euo pipefail

host=${1:?usage: leak.sh HOST [MiB_PER_MIN]}
rate=${2:-30}
here=$(cd "$(dirname "$0")" && pwd)
if [ -n "${GUNGNIR_SERVER:-}" ]; then
    read -r -a server <<<"$GUNGNIR_SERVER"
else
    server=(docker compose -f "$here/../deploy/compose.yaml" exec -T server gungnir-server)
fi
max_h=6
uid=$(id -u)
apps=/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/app.slice
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run=$stamp-leak
runs=$here/runs
label_file=$runs/$run.jsonl
unit=gungnir-sc-leak
victim_unit=gungnir-sc-victim-leak
mkdir -p "$runs"

# The leak never takes more than a gigabyte past what was available at its
# start, so that the host swaps but nothing is killed.
cap_mib=$(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1024 + 1024 ))
leaker=$(cat <<EOF
import time
held = []
while len(held) * $rate < $cap_mib:
    block = bytearray($rate << 20)
    block[::4096] = b"\x01" * (len(block) // 4096)
    held.append(block)
    time.sleep(60)
while True:
    time.sleep(60)
EOF
)

systemd-inhibit --what=idle:sleep:handle-lid-switch --who=gungnir --why="leak scenario" sleep infinity &
inhibitor=$!
cleanup() {
    systemctl --user stop "$unit.scope" "$victim_unit.scope" 2>/dev/null || true
    kill "$inhibitor" 2>/dev/null || true
}
trap cleanup EXIT
systemctl --user reset-failed "$unit.scope" "$victim_unit.scope" 2>/dev/null || true

now_ms() { date +%s%3N; }
proposal_for() {
    "${server[@]}" proposals list "$host" 2>/dev/null |
        awk -F'\t' -v u="/$1.scope" 'substr($4, length($4) - length(u) + 1) == u { print $1, $3, $5; exit }' ||
        true
}

echo "Leak $run on $host: $rate MiB a minute up to $cap_mib MiB, at most $max_h h"
systemd-run --user --scope --quiet --unit="$victim_unit" -- \
    stress-ng --vm 1 --vm-bytes 512m --vm-keep --timeout $(( max_h * 3600 ))s >/dev/null 2>&1 &
sleep 20
start_ms=$(now_ms)
systemd-run --user --scope --quiet --unit="$unit" -- python3 -c "$leaker" >/dev/null 2>&1 &
deadline=$(( $(date +%s) + max_h * 3600 ))
confirmed=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 30
    if [ -z "$confirmed" ]; then
        found=$(proposal_for "$unit")
        if [ -n "$found" ]; then
            read -r id action value <<<"$found"
            echo "  $(date +%H:%M:%S) proposal $id, $action $value; confirming"
            "${server[@]}" proposals confirm "$host" "$id" --by "scenario $run" || true
            confirmed=$id
        fi
    elif "${server[@]}" actions "$host" --hours 1 | awk -F'\t' -v t="/$unit.scope" \
        'substr($5, length($5) - length(t) + 1) == t && $7 != "-" { found = 1 } END { exit !found }'; then
        echo "  $(date +%H:%M:%S) the action is verified"
        sleep 120
        break
    fi
    printf '  %s %s MiB\n' "$(date +%H:%M:%S)" "$(( $(cat "$apps/$unit.scope/memory.current" 2>/dev/null || echo 0) >> 20 ))"
done
end_ms=$(now_ms)
printf '{"run":"%s","kind":"scenario","phase":"memory_leak","start_ms":%s,"end_ms":%s,"normal":false,"expected":["cgroup.mem_bytes","mem.available_bytes"],"scope":"%s.scope","command":"python3 leak %s MiB/min","cause":"memory_leak","harm":true}\n' \
    "$run" "$start_ms" "$end_ms" "$unit" "$rate" >"$label_file"
"${server[@]}" runs import "$host" - <"$label_file"
"${server[@]}" actions "$host" --hours $(( max_h + 1 ))
