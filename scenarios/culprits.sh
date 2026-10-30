#!/bin/bash
# Culprit scenarios (slice S5): in each, one program harms another, and the
# reasoner must name the cause and the program. A victim starts first in
# its own scope gungnir-sc-victim-<cause>, the culprit then runs in
# gungnir-sc-<cause>; each phase is labeled with the cause it should give.
# Both scopes are new groups, judged by the cold start of ADR 0045.
#
# Usage: culprits.sh HOST [cpu] [disk] [memory]   (default: all three; needs
# stress-ng, fio)
#
# The memory scenario takes more than the available memory for a minute:
# the desktop lags and other programs are pushed into swap. The disk
# scenario writes up to about 120 GB into a 12 GB file.
set -euo pipefail

host=${1:?usage: culprits.sh HOST [cpu] [disk] [memory]}
shift
wanted=${*:-cpu disk memory}
here=$(cd "$(dirname "$0")" && pwd)
if [ -n "${GUNGNIR_CORE:-}" ]; then
    read -r -a core <<<"$GUNGNIR_CORE"
else
    core=(docker compose -f "$here/../deploy/compose.yaml" exec -T core gungnir-core)
fi
step_s=${SC_STEP_S:-60}
# The victim runs alone first, so that its own start is not the culprit's.
lead_s=20
# Between scenarios: records close and the windows of the rules (up to two
# minutes after the anchor) run out.
gap_s=120
cpus=$(nproc)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run=$stamp-culprits
runs=$here/runs
label_file=$runs/$run.jsonl

for tool in stress-ng fio systemd-run; do
    command -v "$tool" >/dev/null || { echo "$tool is not installed" >&2; exit 1; }
done
mkdir -p "$runs"
scratch=$(mktemp -d "$runs/.scratch.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

now_ms() { date +%s%3N; }
in_scope() { systemd-run --user --scope --quiet --unit="$1" -- "${@:2}" >/dev/null; }

# scenario CAUSE EXPECTED VICTIM_COMMAND -- CULPRIT_COMMAND
scenario() {
    local cause=$1 expected=$2
    shift 2
    local victim=() culprit=()
    while [ "$1" != "--" ]; do victim+=("$1"); shift; done
    shift
    culprit=("$@")
    echo "  $cause: victim ${victim[*]}"
    in_scope "gungnir-sc-victim-$cause" "${victim[@]}" &
    local pid=$!
    sleep "$lead_s"
    echo "  $cause: culprit ${culprit[*]}"
    local start_ms end_ms
    start_ms=$(now_ms)
    in_scope "gungnir-sc-$cause" "${culprit[@]}"
    end_ms=$(now_ms)
    wait "$pid"
    printf '{"run":"%s","kind":"scenario","phase":"%s","start_ms":%s,"end_ms":%s,"normal":false,"expected":%s,"scope":"gungnir-sc-%s","command":"%s"}\n' \
        "$run" "$cause" "$start_ms" "$end_ms" "$expected" "$cause" "${culprit[*]}" >>"$label_file"
    sleep "$gap_s"
}

victim_s=$(( step_s + 2 * lead_s ))
echo "Culprit scenarios $run on $host: $wanted"
for which in $wanted; do
    case $which in
    cpu)
        # A light victim (0.3 cores, below the noticeable 0.5) waits for CPU
        # once every CPU is busy.
        scenario cpu_hog '["cgroup.cpu_cores","cpu.idle_ratio","cgroup.cpu_pressure_some_ratio"]' \
            stress-ng --cpu 1 --cpu-load 30 --timeout "${victim_s}s" -- \
            stress-ng --cpu "$cpus" --timeout "${step_s}s"
        ;;
    disk)
        # A buffered writer fills the page cache past the background
        # writeback threshold; the victim writes small blocks and calls fsync
        # after each, so it waits behind the flood. Direct writes did not
        # harm anyone on the laptop's NVMe: a 4 KiB reader or fsync writer
        # waited 2-4 % beside 2 GB/s with 32 requests in flight
        # (run 20261030T193858Z and trials of 2026-10-30).
        scenario writeback_storm \
            '["mem.dirty_bytes","disk.write_bytes_per_s","cgroup.io_pressure_some_ratio"]' \
            fio --name=victim --filename="$scratch/victim.dat" --size=64m --rw=randwrite --bs=4k --fsync=1 \
            --rate_iops=200 --time_based --runtime="$victim_s" -- \
            fio --name=writer --filename="$scratch/writer.dat" --size=12g --rw=write --bs=1m \
            --time_based --runtime="$step_s"
        ;;
    memory)
        # The culprit takes a gigabyte more than is available; the victim
        # keeps touching 512 MiB of its own and stalls when it is reclaimed.
        avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
        scenario memory_hog '["cgroup.mem_bytes","mem.available_bytes","cgroup.mem_pressure_some_ratio"]' \
            stress-ng --vm 1 --vm-bytes 512m --vm-keep --timeout "${victim_s}s" -- \
            stress-ng --vm 1 --vm-bytes $(( avail_kb + 1024 * 1024 ))k --vm-keep --timeout "${step_s}s"
        ;;
    *) echo "unknown scenario $which" >&2; exit 1 ;;
    esac
done
echo "Phases recorded in $label_file"

"${core[@]}" runs import "$host" - <"$label_file"
echo "Diagnose them with: gungnir-core diagnose $host --run $run"
