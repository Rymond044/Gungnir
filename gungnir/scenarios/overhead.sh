#!/bin/bash
# Measures the agent's own CPU and memory use (NFR-1) while the host is idle
# and under load. Reads the agent unit's control group, so the journal
# reader that the agent runs as a child is included. Each phase is written
# to a label file, so the loaded windows can later be found in the data.
#
# Usage: overhead.sh [SECONDS_PER_PHASE]     (default 120; needs stress-ng)
set -euo pipefail

phase_s=${1:-120}
unit=gungnir-agent.service
cg=/sys/fs/cgroup/system.slice/$unit
runs=$(dirname "$0")/runs
run=$(date -u +%Y%m%dT%H%M%SZ)-overhead
label_file=$runs/$run.jsonl
cpus=$(nproc)
mem_load=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 4 ))k
command -v stress-ng >/dev/null || { echo "stress-ng is not installed" >&2; exit 1; }
[ -r "$cg/cpu.stat" ] || { echo "$unit is not running" >&2; exit 1; }
mkdir -p "$runs"
# On the real disk: /tmp is often a tmpfs, which would load memory instead.
scratch=$(mktemp -d "$runs/.scratch.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

usage_us() { awk '/^usage_usec/ {print $2}' "$cg/cpu.stat"; }
# Private (anonymous) resident memory of every process in the unit, in
# bytes. VmRSS would also count mapped journal files, which are page cache
# shared with the rest of the system and reclaimable.
rss() {
    local total=0 kb pid
    while read -r pid; do
        kb=$(awk '/^RssAnon/ {print $2}' "/proc/$pid/status" 2>/dev/null || echo 0)
        total=$(( total + ${kb:-0} * 1024 ))
    done <"$cg/cgroup.procs"
    echo "$total"
}
now_ms() { date +%s%3N; }

# One phase: run the load (if any) for phase_s seconds while sampling the
# unit's memory every second; print one table row.
phase() {
    local name=$1; shift
    local start_ms start_us peak=0 sum=0 n=0 mem cg_peak=0 pid=""
    if [ $# -gt 0 ]; then
        "$@" >/dev/null 2>&1 &
        pid=$!
    fi
    start_ms=$(now_ms)
    start_us=$(usage_us)
    for _ in $(seq "$phase_s"); do
        sleep 1
        mem=$(rss)
        sum=$(( sum + mem )); n=$(( n + 1 ))
        [ "$mem" -gt "$peak" ] && peak=$mem
        mem=$(cat "$cg/memory.current")
        [ "$mem" -gt "$cg_peak" ] && cg_peak=$mem
    done
    local end_us end_ms
    end_us=$(usage_us)
    end_ms=$(now_ms)
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true

    local normal=false command=null
    [ "$name" = idle ] && normal=true
    [ $# -gt 0 ] && command="\"$*\""
    printf '{"run":"%s","kind":"overhead","phase":"%s","start_ms":%s,"end_ms":%s,"normal":%s,"expected":%s,"command":%s}\n' \
        "$run" "$name" "$start_ms" "$end_ms" "$normal" "${expected[$name]}" "$command" >>"$label_file"
    awk -v name="$name" -v us=$(( end_us - start_us )) -v ms=$(( end_ms - start_ms )) \
        -v cpus="$cpus" -v avg=$(( sum / n )) -v peak="$peak" -v cg_peak="$cg_peak" 'BEGIN {
        core = us / (ms * 1000) * 100
        printf "| %-8s | %6.2f | %6.3f | %7.1f | %7.1f | %8.1f |\n", name, core, core / cpus,
            avg / 1048576, peak / 1048576, cg_peak / 1048576
    }'
}

# Signals each phase should show up in, for evaluation (labels.py).
declare -A expected=(
    [idle]='[]'
    [cpu]='["cpu.user_ratio","cpu.idle_ratio","cpu.pressure_some_ratio"]'
    [memory]='["mem.available_bytes","mem.anon_bytes","mem.used_ratio"]'
    [disk]='["disk.write_bytes_per_s","disk.util_ratio"]'
    [fork]='["cpu.forks_per_s","proc.count"]'
)
load_for="--timeout ${phase_s}s"
echo "Agent overhead, $phase_s s per phase, $cpus CPUs, $(date -u +%FT%TZ)"
echo "| phase    | % core | % host | RSS avg | RSS max | unit max |"
echo "|----------|--------|--------|---------|---------|----------|"
phase idle
phase cpu    stress-ng --cpu "$cpus" $load_for
phase memory stress-ng --vm 2 --vm-bytes "$mem_load" $load_for
phase disk   stress-ng --hdd 2 --hdd-bytes 1g --temp-path "$scratch" $load_for
# Many short-lived processes: the worst case for the process snapshot.
phase fork   stress-ng --fork 4 $load_for
echo "% core: share of one CPU; RSS: private resident memory of the agent and journalctl, MiB;"
echo "unit max: the unit's memory.current (RSS plus page cache charged to the unit), MiB."
echo "Phases recorded in $label_file"
