#!/bin/bash
# The reference scenario С-1 (slice S9): a program leaks memory slowly in
# its own scope, the growth check sees it after three rising hours (ADR
# 0058), the host runs short of memory and a victim waits; the incident
# should name memory_leak with the scope as culprit and propose memory.high.
# The script confirms that proposal as actions.sh does and stops the leak
# once the action is verified, or half an hour after it reached its cap.
#
# Whether the leak hurt someone is measured, as in suite.sh: the victim's
# stall on memory over the last `harm_window_s`, harmful from `harm_share`
# on. The peak share goes to the label.
#
# Usage: leak.sh HOST [MiB_PER_MIN]   (default 30: 5 h for 9 GiB available)
set -euo pipefail

host=${1:?usage: leak.sh HOST [MiB_PER_MIN]}
rate=${2:-30}
here=$(cd "$(dirname "$0")" && pwd)
if [ -n "${GUNGNIR_SERVER:-}" ]; then
    read -r -a server <<<"$GUNGNIR_SERVER"
else
    server=(docker compose -f "$here/../deploy/compose.yaml" exec -T server gungnir-server)
fi
harm_share=0.10
harm_window_s=120
uid=$(id -u)
apps=/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/app.slice
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run=$stamp-leak
runs=$here/runs
label_file=$runs/$run.jsonl
harm_file=$runs/$run.harm.tsv
# A scope's baseline is its own history: a name an earlier run used would
# read that run's gigabytes as usual. The stamp sits inside the name, where
# the agent does not strip it.
unit=gungnir-sc-$stamp-leak
victim_unit=gungnir-sc-$stamp-victim-leak
mkdir -p "$runs"

# The leak grows `over_mib` past what was available at its start: past the
# point where the victim waits, far enough for memory.high at 110 % of the
# leak (knowledge/policy/actions.toml) to bind while it still grows. Swap
# must hold that much and a gigabyte more, so that nothing is killed.
over_mib=3072
avail_mib=$(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1024 ))
swap_free_mib=$(( $(awk '/SwapFree/ {print $2}' /proc/meminfo) / 1024 ))
[ "$swap_free_mib" -gt $(( over_mib + 1024 )) ] ||
    { echo "need $(( over_mib + 1024 )) MiB of free swap, have $swap_free_mib" >&2; exit 1; }
cap_mib=$(( avail_mib + over_mib ))
max_s=$(( cap_mib / rate * 60 + 1800 ))
# The leak touches every page it holds once a minute: pages it never
# touched again went to swap unnoticed and nobody waited (night run
# 20261116T202328Z-leak).
leaker=$(cat <<EOF
import time
held = []
while True:
    if len(held) * $rate < $cap_mib:
        block = bytearray($rate << 20)
        held.append(block)
    for block in held:
        block[::4096] = b"\x01" * (len(block) // 4096)
    time.sleep(60)
EOF
)

systemd-inhibit --what=idle:sleep:handle-lid-switch --who=gungnir --why="leak scenario" sleep infinity &
inhibitor=$!
cleanup() {
    # One by one: a scope that has already ended fails the stop of the rest.
    systemctl --user stop "$unit.scope" 2>/dev/null || true
    systemctl --user stop "$victim_unit.scope" 2>/dev/null || true
    kill "$inhibitor" 2>/dev/null || true
}
trap cleanup EXIT

now_ms() { date +%s%3N; }
# The victim's stall on memory so far, in microseconds ("some" total).
stall_us() {
    awk '/^some/ { for (i = 1; i <= NF; i++) if ($i ~ /^total=/) { sub("total=", "", $i); print $i } }' \
        "$apps/$victim_unit.scope/memory.pressure" 2>/dev/null || echo 0
}
proposal_for() {
    "${server[@]}" proposals list "$host" 2>/dev/null |
        awk -F'\t' -v u="/$1.scope" 'substr($4, length($4) - length(u) + 1) == u { print $1, $3, $5; exit }' ||
        true
}

echo "Leak $run on $host: $rate MiB a minute up to $cap_mib MiB, at most $(( max_s / 60 )) min"
# The victim outlives the script, so that its stall is read to the end.
systemd-run --user --scope --quiet --unit="$victim_unit" -- \
    stress-ng --vm 1 --vm-bytes 512m --vm-keep --timeout $(( max_s + 600 ))s >/dev/null 2>&1 &
sleep 20
start_ms=$(now_ms)
systemd-run --user --scope --quiet --unit="$unit" -- python3 -c "$leaker" >/dev/null 2>&1 &
deadline=$(( $(date +%s) + max_s ))
confirmed=""
times=() stalls=() peak=0
while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 30
    times+=("$(now_ms)")
    stalls+=("$(stall_us)")
    n=${#times[@]} back=$(( harm_window_s / 30 ))
    if [ "$n" -gt "$back" ]; then
        share=$(awk -v a="${stalls[n - 1]}" -v b="${stalls[n - 1 - back]}" \
            -v d=$(( (times[n - 1] - times[n - 1 - back]) * 1000 )) 'BEGIN { printf "%.3f", (a - b) / d }')
        peak=$(awk -v s="$share" -v p="$peak" 'BEGIN { print (s > p) ? s : p }')
    fi
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
    printf '  %s %s MiB, victim stalled %s of the last %s s\n' "$(date +%H:%M:%S)" \
        "$(( $(cat "$apps/$unit.scope/memory.current" 2>/dev/null || echo 0) >> 20 ))" "${share:-0}" "$harm_window_s"
done
end_ms=$(now_ms)
harm=$(awk -v s="$peak" -v t="$harm_share" 'BEGIN { print (s >= t) ? "true" : "false" }')
printf 'memory_leak\t-\tmemory\t%s\t%s\n' "$peak" "$harm" >"$harm_file"
printf '{"run":"%s","kind":"scenario","phase":"memory_leak","start_ms":%s,"end_ms":%s,"normal":false,"expected":["cgroup.mem_bytes","mem.available_bytes"],"scope":"%s.scope","command":"python3 leak %s MiB/min","cause":"memory_leak","harm":%s}\n' \
    "$run" "$start_ms" "$end_ms" "$unit" "$rate" "$harm" >"$label_file"
echo "Victim stalled at most $peak of $harm_window_s s on memory; harm $harm"
"${server[@]}" runs import "$host" - <"$label_file"
"${server[@]}" actions "$host" --hours $(( max_s / 3600 + 1 ))
