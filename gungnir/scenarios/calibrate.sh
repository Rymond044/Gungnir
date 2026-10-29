#!/bin/bash
# Calibration run (ADR 0024): about 15 minutes of graded loads, each in its
# own scope gungnir-cal-<load>-<step>, with the host otherwise left alone.
# The script only creates load and writes one label per phase; the core
# measures capacity from the samples its agent sent meanwhile.
#
# Usage: calibrate.sh HOST     (HOST as known to the core; needs stress-ng, fio)
#
# At the end the labels are imported, capacity is derived and processes no
# workload rule covers are listed, through GUNGNIR_CORE (default: the core
# container of deploy/compose.yaml).
set -euo pipefail

host=${1:?usage: calibrate.sh HOST}
here=$(cd "$(dirname "$0")" && pwd)
core=${GUNGNIR_CORE:-docker compose -f $here/../deploy/compose.yaml exec -T core gungnir-core}
idle_s=${CAL_IDLE_S:-300}
step_s=${CAL_STEP_S:-60}
# Between phases, so that one load fades before the next starts.
gap_s=15
cpus=$(nproc)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run=$stamp-calibration
runs=$here/runs
label_file=$runs/$run.jsonl

for tool in stress-ng fio systemd-run; do
    command -v "$tool" >/dev/null || { echo "$tool is not installed" >&2; exit 1; }
done
mkdir -p "$runs"
# On the real disk: /tmp is often a tmpfs, which would load memory instead.
scratch=$(mktemp -d "$runs/.scratch.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
free_kb=$(df -Pk "$scratch" | awk 'NR == 2 {print $4}')
[ "$free_kb" -gt $(( 4 * 1024 * 1024 )) ] || { echo "need 4 GiB free next to $scratch" >&2; exit 1; }

now_ms() { date +%s%3N; }

# phase NAME NORMAL EXPECTED [COMMAND...]: runs the command in its own
# scope (or sleeps for an idle phase) and appends the label.
phase() {
    local name=$1 normal=$2 expected=$3 seconds
    shift 3
    local start_ms end_ms scope=null command=null
    start_ms=$(now_ms)
    if [ $# -gt 0 ]; then
        echo "  $name: $*"
        systemd-run --user --scope --quiet --unit="gungnir-cal-$name" -- "$@" >/dev/null
        scope="\"gungnir-cal-$name\""
        command="\"$*\""
    else
        seconds=$idle_s
        echo "  $name: ${seconds}s, leave the host alone"
        sleep "$seconds"
    fi
    end_ms=$(now_ms)
    printf '{"run":"%s","kind":"calibration","phase":"%s","start_ms":%s,"end_ms":%s,"normal":%s,"expected":%s,"scope":%s,"command":%s}\n' \
        "$run" "$name" "$start_ms" "$end_ms" "$normal" "$expected" "$scope" "$command" >>"$label_file"
    sleep "$gap_s"
}

cpu='["cpu.user_ratio","cpu.idle_ratio","cpu.pressure_some_ratio","cpu.runnable_tasks"]'
mem='["mem.available_bytes","mem.anon_bytes","mem.used_ratio"]'
for_step="--timeout ${step_s}s"
# Direct I/O measures the device, not the page cache.
fio_direct=(--filename="$scratch/fio.dat" --size=2g --direct=1 --ioengine=psync --time_based --runtime="$step_s")
avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)

echo "Calibration run $run on $host ($cpus CPUs), about $(( (idle_s + 10 * (step_s + gap_s)) / 60 )) min"
phase idle true '[]'
phase cpu-25 false "$cpu" stress-ng --cpu $(( (cpus + 3) / 4 )) $for_step
phase cpu-50 false "$cpu" stress-ng --cpu $(( (cpus + 1) / 2 )) $for_step
phase cpu-100 false "$cpu" stress-ng --cpu "$cpus" $for_step
# Shares of available memory, so the load never pushes into swap.
phase mem-25 false "$mem" stress-ng --vm 1 --vm-bytes $(( avail_kb / 4 ))k --vm-keep $for_step
phase mem-50 false "$mem" stress-ng --vm 1 --vm-bytes $(( avail_kb / 2 ))k --vm-keep $for_step
# Sequential write first: it lays out the file the read phases use.
phase disk-seqwrite false '["disk.write_bytes_per_s","disk.util_ratio"]' \
    fio --name=seqwrite "${fio_direct[@]}" --rw=write --bs=1m
phase disk-seqread false '["disk.read_bytes_per_s","disk.util_ratio"]' \
    fio --name=seqread "${fio_direct[@]}" --rw=read --bs=1m
phase disk-randwrite false '["disk.write_ops_per_s","disk.write_await_ms"]' \
    fio --name=randwrite "${fio_direct[@]}" --rw=randwrite --bs=4k
phase disk-randread false '["disk.read_ops_per_s","disk.read_await_ms"]' \
    fio --name=randread "${fio_direct[@]}" --rw=randread --bs=4k
# Buffered writes, each followed by fsync: the pattern of databases and editors.
phase disk-fsync false '["disk.write_await_ms","disk.write_ops_per_s","io.pressure_some_ratio"]' \
    fio --name=fsync --filename="$scratch/fio.dat" --size=2g --rw=write --bs=4k --fsync=1 \
    --time_based --runtime="$step_s"
echo "Phases recorded in $label_file"

# The last batch reaches the core within about 10 s.
sleep 15
$core runs import "$host" - <"$label_file"
$core calibrate "$host" "$run"
$core workload unmatched "$host"
