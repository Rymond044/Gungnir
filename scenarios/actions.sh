#!/bin/bash
# Scenarios that end with a confirmed action (slice S9): a culprit hurts a
# victim long enough for the incident, its proposal, the confirmation, the
# actuator and the verification window. The script confirms the first
# proposal against the culprit as an operator on the server's machine
# (`proposals confirm --by "scenario <run>"`), so the action log says who
# decided. Whether the action helped is also measured here, from the
# victim's own pressure counters before the confirmation and over the last
# minute of the culprit.
#
#   cpu_hog          a hog delays a program on 12 of 18 CPUs; cpu.weight should help
#   writeback_storm  a buffered writer delays a program calling fsync; io.max should help
#   two_hogs         two hogs, the action limits one, the victim still waits:
#                    verification fails and the action is rolled back
#
# Usage: actions.sh HOST [cpu_hog] [writeback_storm] [two_hogs]   (needs stress-ng, fio)
set -euo pipefail

host=${1:?usage: actions.sh HOST [cpu_hog] [writeback_storm] [two_hogs]}
shift
wanted=${*:-cpu_hog writeback_storm two_hogs}
here=$(cd "$(dirname "$0")" && pwd)
if [ -n "${GUNGNIR_SERVER:-}" ]; then
    read -r -a server <<<"$GUNGNIR_SERVER"
else
    server=(docker compose -f "$here/../deploy/compose.yaml" exec -T server gungnir-server)
fi
lead_s=20
# A heavy CPU victim runs longer alone, past the rule's window, as in suite.sh.
cpu_lead_s=90
gap_s=180
# How long the script waits for a proposal against the culprit.
propose_s=180
cpus=$(nproc)
uid=$(id -u)
apps=/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/app.slice
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run=$stamp-actions
runs=$here/runs
label_file=$runs/$run.jsonl
result_file=$runs/$run.tsv

for tool in stress-ng fio systemd-run systemd-inhibit; do
    command -v "$tool" >/dev/null || { echo "$tool is not installed" >&2; exit 1; }
done
mkdir -p "$runs"
scratch=$(mktemp -d "$runs/.scratch.XXXXXX")
systemd-inhibit --what=idle:sleep:handle-lid-switch --who=gungnir --why="action scenarios" sleep infinity &
inhibitor=$!
trap 'kill $inhibitor 2>/dev/null; rm -rf "$scratch"' EXIT

now_ms() { date +%s%3N; }
stall_us() {
    awk '/^some/ { for (i = 1; i <= NF; i++) if ($i ~ /^total=/) { sub("total=", "", $i); print $i } }' \
        "$apps/$1.scope/$2.pressure" 2>/dev/null || echo 0
}
share() { awk -v a="$1" -v b="$2" -v d="$3" 'BEGIN { printf "%.3f", (a - b) / (d * 1000) }'; }

# The first waiting proposal against the unit, as "id action value".
proposal_for() {
    "${server[@]}" proposals list "$host" 2>/dev/null |
        awk -F'\t' -v u="/$1.scope" 'substr($4, length($4) - length(u) + 1) == u { print $1, $3, $5; exit }' ||
        true
}

# act NAME CAUSE RESOURCE SECONDS VICTIM... -- CULPRIT... [-- SECOND CULPRIT...]
act() {
    local name=$1 cause=$2 resource=$3 seconds=$4
    shift 4
    local victim=() culprit=() other=()
    while [ "$1" != "--" ]; do victim+=("$1"); shift; done
    shift
    while [ $# -gt 0 ] && [ "$1" != "--" ]; do culprit+=("$1"); shift; done
    [ $# -gt 0 ] && { shift; other=("$@"); }
    local unit=gungnir-sc-act-$name victim_unit=gungnir-sc-act-victim-$name
    systemctl --user reset-failed "$unit.scope" "gungnir-sc-act-other-$name.scope" "$victim_unit.scope" 2>/dev/null ||
        true
    systemd-run --user --scope --quiet --unit="$victim_unit" -- "${victim[@]}" >/dev/null 2>&1 &
    local vpid=$!
    if [ "$resource" = cpu ]; then sleep "$cpu_lead_s"; else sleep "$lead_s"; fi
    echo "  $(date +%H:%M:%S) $name: ${culprit[*]}${other:+ and a second one}"
    local start_ms s0 cpid opid=
    start_ms=$(now_ms)
    s0=$(stall_us "$victim_unit" "$resource")
    systemd-run --user --scope --quiet --unit="$unit" -- "${culprit[@]}" >/dev/null 2>&1 &
    cpid=$!
    if [ ${#other[@]} -gt 0 ]; then
        # Not "$unit-2": the agent folds numbered scopes of one name into one
        # program, and the action would hold both.
        systemd-run --user --scope --quiet --unit="gungnir-sc-act-other-$name" -- "${other[@]}" >/dev/null 2>&1 &
        opid=$!
    fi
    local found="" waited=0
    while [ -z "$found" ] && [ "$waited" -lt "$propose_s" ]; do
        sleep 5
        waited=$((waited + 5))
        found=$(proposal_for "$unit")
    done
    local confirm_ms s1 id=- action=- value=-
    confirm_ms=$(now_ms)
    s1=$(stall_us "$victim_unit" "$resource")
    if [ -n "$found" ]; then
        read -r id action value <<<"$found"
        echo "    after ${waited}s: proposal $id, $action $value; confirming"
        "${server[@]}" proposals confirm "$host" "$id" --by "scenario $run" || true
    else
        echo "    no proposal against $unit within ${propose_s}s"
    fi
    # The last minute of the culprit is the "after".
    local left=$(( seconds - (confirm_ms - start_ms) / 1000 - 60 ))
    [ "$left" -gt 0 ] && sleep "$left"
    local s2 m2 s3 end_ms
    s2=$(stall_us "$victim_unit" "$resource")
    m2=$(now_ms)
    wait "$cpid" || true
    [ -n "$opid" ] && { wait "$opid" || true; }
    end_ms=$(now_ms)
    s3=$(stall_us "$victim_unit" "$resource")
    wait "$vpid" || true
    local before after
    before=$(share "$s1" "$s0" $(( confirm_ms - start_ms )))
    after=$(share "$s3" "$s2" $(( end_ms - m2 )))
    echo "    victim stalled $before before the confirmation, $after in the last minute"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$id" "$action" "$value" "$before" "$after" >>"$result_file"
    printf '{"run":"%s","kind":"action","phase":"%s","start_ms":%s,"end_ms":%s,"normal":false,"expected":[],"scope":"%s.scope","command":"%s","cause":"%s","harm":true}\n' \
        "$run" "$name" "$start_ms" "$end_ms" "$unit" "${culprit[*]}" "$cause" >>"$label_file"
    sleep "$gap_s"
}

heavy_victim=(stress-ng --cpu $(( cpus * 2 / 3 )))
fsync_victim=(fio --name=victim --filename="$scratch/victim.dat" --size=64m --rw=randwrite --bs=4k --fsync=1
    --rate_iops=200 --time_based)
echo "Action scenarios $run on $host: $wanted"
for which in $wanted; do
    case $which in
    cpu_hog)
        # Verification waits for the longest window of cpu_hog, 5 min.
        act cpu_hog cpu_hog cpu 480 "${heavy_victim[@]}" --timeout $(( 480 + cpu_lead_s + lead_s ))s -- \
            stress-ng --cpu $(( 2 * cpus )) --timeout 480s ;;
    writeback_storm)
        act writeback_storm writeback_storm io 300 "${fsync_victim[@]}" --runtime=$(( 300 + 2 * lead_s )) -- \
            fio --name=writer --filename="$scratch/writer.dat" --size=12g --rw=write --bs=1m \
            --time_based --runtime=300 ;;
    two_hogs)
        act two_hogs cpu_hog cpu 480 "${heavy_victim[@]}" --timeout $(( 480 + cpu_lead_s + lead_s ))s -- \
            stress-ng --cpu $(( 2 * cpus )) --timeout 480s -- stress-ng --cpu $(( 2 * cpus )) --timeout 480s ;;
    *) echo "unknown scenario $which" >&2; exit 1 ;;
    esac
done
"${server[@]}" runs import "$host" - <"$label_file"
echo "Measured: $result_file"
"${server[@]}" actions "$host" --hours 2
