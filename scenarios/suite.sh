#!/bin/bash
# Fault scenarios with ground truth (slice S9, ADR 0008): each phase injects
# one known cause or runs a load that should hurt nobody, and its label says
# which cause the inference engine should name and whether an incident
# should open. `gungnir-server replay HOST live` and `compare HOST` score
# thresholds, statistics and the hybrid against them.
#
# Whether a phase hurt someone is measured here, not by detection: a victim
# runs in its own scope before and after the culprit, and the phase is
# harmful when the victim stalled at least `harm_share` of the culprit's
# time on the scenario's resource (the kernel's pressure counters of its
# control group, the noticeable impact level of knowledge/detection/impact.toml).
# A group held by its own limit (quota, memory.high, memory.max) is hurt by
# construction: the limit is below what it asks for.
#
# Usage: suite.sh HOST [--pilot] [--repeat N] [--level L] [scenario ...]
#   --pilot      one pass at the middle intensity
#   --repeat N   N passes (default 5), intensity cycling low, middle, high
#   --level L    every pass at intensity L (0 low, 1 middle, 2 high)
# Needs stress-ng, fio; writes up to about 130 GB per pass to the disk of
# scenarios/runs. The memory scenarios swap for a minute.
set -euo pipefail

usage="usage: suite.sh HOST [--pilot] [--repeat N] [--level L] [scenario ...]"
host=${1:?$usage}
shift
repeat=5
first_level=0
fixed_level=
while [ $# -gt 0 ]; do
    case $1 in
    --pilot) repeat=1; first_level=1; shift ;;
    --repeat) repeat=${2:?$usage}; shift 2 ;;
    --level) fixed_level=${2:?$usage}; shift 2 ;;
    -*) echo "$usage" >&2; exit 1 ;;
    *) break ;;
    esac
done
# Phases that load memory stand at least two phases apart: the memory rules
# look up to five minutes back for the group that grew, and would bind the
# previous phase's scope.
all="harmless_memory cpu_hog cpu_quota_throttling memory_hog bulk_reader process_churn
     group_memory_throttled small_random_io bulk_sequential_writer swap_io sync_write_storm
     writeback_storm group_memory_limit harmless_cpu harmless_copy harmless_build"
wanted=${*:-$all}

here=$(cd "$(dirname "$0")" && pwd)
if [ -n "${GUNGNIR_SERVER:-}" ]; then
    read -r -a server <<<"$GUNGNIR_SERVER"
else
    server=(docker compose -f "$here/../deploy/compose.yaml" exec -T server gungnir-server)
fi
step_s=${SC_STEP_S:-60}
# The victim runs alone first, so that its own start is not the culprit's.
# A victim that is itself heavy (CPU) runs longer first, past the rule's
# window, as a long job does before a runaway joins it.
lead_s=20
cpu_lead_s=90
# Between phases: harm records close, the incident's two quiet minutes pass
# and the rules' windows after the leading symptom run out.
gap_s=${SC_GAP_S:-150}
harm_share=0.10
cpus=$(nproc)
uid=$(id -u)
apps=/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/app.slice
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run=$stamp-suite
runs=$here/runs
label_file=$runs/$run.jsonl
harm_file=$runs/$run.harm.tsv

for tool in stress-ng fio systemd-run systemd-inhibit; do
    command -v "$tool" >/dev/null || { echo "$tool is not installed" >&2; exit 1; }
done
mkdir -p "$runs"
# On the real disk: /tmp is often a tmpfs, which would load memory instead.
scratch=$(mktemp -d "$runs/.scratch.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
free_kb=$(df -Pk "$scratch" | awk 'NR == 2 {print $4}')
[ "$free_kb" -gt $(( 20 * 1024 * 1024 )) ] || { echo "need 20 GiB free next to $scratch" >&2; exit 1; }

now_ms() { date +%s%3N; }
# The stall of a group on a resource so far, in microseconds ("some" total).
stall_us() {
    awk '/^some/ { for (i = 1; i <= NF; i++) if ($i ~ /^total=/) { sub("total=", "", $i); print $i } }' \
        "$apps/$1.scope/$2.pressure" 2>/dev/null || echo 0
}

# phase NAME CAUSE HARM EXPECTED RESOURCE [VICTIM... --] [PROPS... ---] CULPRIT...
# HARM: "measure" (from the victim), true (a group held by its own limit) or
# false (nobody is put at risk). CAUSE "-" for none. RESOURCE: cpu, io or memory.
phase() {
    local name=$1 cause=$2 harm=$3 expected=$4 resource=$5
    shift 5
    local victim=() props=() culprit=() args=("$@") i=0
    if [[ " ${args[*]} " == *" -- "* ]]; then
        while [ "${args[$i]}" != "--" ]; do victim+=("${args[$i]}"); i=$((i + 1)); done
        i=$((i + 1))
    fi
    if [[ " ${args[*]:$i} " == *" --- "* ]]; then
        while [ "${args[$i]}" != "---" ]; do props+=(-p "${args[$i]}"); i=$((i + 1)); done
        i=$((i + 1))
    fi
    culprit=("${args[@]:$i}")
    # Each pass is a program of its own: a scope's baseline is its own
    # history, and a name used by an earlier pass would read its load as
    # usual (night run 20261115T173130Z, writeback_storm-5). Stamp and pass
    # sit inside the name, where the agent does not strip them.
    local unit=gungnir-sc-${stamp}p$pass-$name victim_unit=gungnir-sc-${stamp}p$pass-victim-$name vpid=
    # A scope that was killed stays failed and would block its name.
    systemctl --user reset-failed "$unit.scope" "$victim_unit.scope" 2>/dev/null || true
    if [ ${#victim[@]} -gt 0 ]; then
        systemd-run --user --scope --quiet --unit="$victim_unit" -- "${victim[@]}" >/dev/null 2>&1 &
        vpid=$!
        if [ "$resource" = cpu ]; then sleep "$cpu_lead_s"; else sleep "$lead_s"; fi
    fi
    echo "  $(date +%H:%M:%S) $name: ${culprit[*]}"
    local before after start_ms end_ms
    before=$( [ -n "$vpid" ] && stall_us "$victim_unit" "$resource" || echo 0)
    start_ms=$(now_ms)
    systemd-run --user --scope --quiet --unit="$unit" "${props[@]}" -- "${culprit[@]}" >/dev/null 2>&1 || true
    end_ms=$(now_ms)
    after=$( [ -n "$vpid" ] && stall_us "$victim_unit" "$resource" || echo 0)
    [ -n "$vpid" ] && wait "$vpid" || true
    local share
    share=$(awk -v a="$after" -v b="$before" -v d=$(( (end_ms - start_ms) * 1000 )) 'BEGIN { printf "%.3f", (a - b) / d }')
    if [ "$harm" = measure ]; then
        harm=$(awk -v s="$share" -v t="$harm_share" 'BEGIN { print (s >= t) ? "true" : "false" }')
    fi
    local cause_json=null scope_json=null
    [ "$cause" != - ] && cause_json="\"$cause\""
    # The culprit is named only where the cause has one; a group held by its
    # own limit is the one hurt.
    [ "$cause" != - ] && [ ${#props[@]} -eq 0 ] && scope_json="\"$unit.scope\""
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$level" "$resource" "$share" "$harm" >>"$harm_file"
    printf '{"run":"%s","kind":"scenario","phase":"%s-%d","start_ms":%s,"end_ms":%s,"normal":false,"expected":%s,"scope":%s,"command":"%s","cause":%s,"harm":%s}\n' \
        "$run" "$name" "$pass" "$start_ms" "$end_ms" "$expected" "$scope_json" "${culprit[*]//\"/}" \
        "$cause_json" "$harm" >>"$label_file"
    echo "    victim stalled $share of the time on $resource; harm $harm"
    sleep "$gap_s"
}

# pick LEVEL LOW MIDDLE HIGH
pick() { local l=$1; shift; echo "${@:$((l + 1)):1}"; }

victim_s=$(( step_s + 2 * lead_s ))
cpu_victim_s=$(( step_s + cpu_lead_s + lead_s ))
t="--timeout ${step_s}s"
fio_time=(--time_based --runtime="$step_s")
# Each scope has the same CPU weight, so a hog in another scope delays only a
# program that wants more than its fair share: one light program stalled
# 1 % beside 36 hog workers, one on 12 of the 18 CPUs 30 % (2026-11-15).
cpu_victim=(stress-ng --cpu $(( cpus * 2 / 3 )) --timeout "${cpu_victim_s}s")
# A program that writes small blocks and calls fsync after each.
fsync_victim=(fio --name=victim --filename="$scratch/victim.dat" --size=64m --rw=randwrite --bs=4k --fsync=1
    --rate_iops=200 --time_based --runtime="$victim_s")
read_victim=(fio --name=victim --filename="$scratch/victim.dat" --size=64m --rw=randread --bs=4k --direct=1
    --rate_iops=200 --time_based --runtime="$victim_s")
mem_victim=(stress-ng --vm 1 --vm-bytes 512m --vm-keep --timeout "${victim_s}s")
cpu_signals='["cgroup.cpu_cores","cpu.idle_ratio","cgroup.cpu_pressure_some_ratio"]'
io_signals='["disk.write_bytes_per_s","disk.read_bytes_per_s","cgroup.io_pressure_some_ratio"]'
mem_signals='["mem.available_bytes","swap.out_pages_per_s","mem.pressure_some_ratio","cgroup.mem_pressure_some_ratio"]'

scenario() {
    local s=$1
    case $s in
    cpu_hog)
        phase cpu_hog cpu_hog measure "$cpu_signals" cpu "${cpu_victim[@]}" -- \
            stress-ng --cpu "$(pick "$level" "$cpus" $((2 * cpus)) $((3 * cpus)))" $t ;;
    cpu_quota_throttling)
        phase cpu_quota_throttling cpu_quota_throttling true '["cgroup.throttled_ratio"]' cpu \
            "CPUQuota=$(pick "$level" 200 100 50)%" --- stress-ng --cpu 4 $t ;;
    process_churn)
        phase process_churn process_churn measure '["cpu.forks_per_s","cpu.system_ratio"]' cpu \
            "${cpu_victim[@]}" -- stress-ng --fork "$(pick "$level" $((cpus / 2)) "$cpus" $((2 * cpus)))" $t ;;
    bulk_reader)
        [ -f "$scratch/big.dat" ] || fio --name=fill --filename="$scratch/big.dat" --size=8g --rw=write --bs=1m \
            --direct=1 >/dev/null
        phase bulk_reader bulk_reader measure "$io_signals" io "${read_victim[@]}" -- \
            fio --name=reader --filename="$scratch/big.dat" --size=8g --rw=read --bs=1m --direct=1 \
            --iodepth="$(pick "$level" 4 16 32)" --ioengine=libaio "${fio_time[@]}" ;;
    small_random_io)
        [ -f "$scratch/big.dat" ] || fio --name=fill --filename="$scratch/big.dat" --size=8g --rw=write --bs=1m \
            --direct=1 >/dev/null
        phase small_random_io small_random_io measure "$io_signals" io "${read_victim[@]}" -- \
            fio --name=random --filename="$scratch/big.dat" --size=8g --rw=randread --bs=4k --direct=1 \
            --iodepth=32 --numjobs="$(pick "$level" 2 4 8)" --ioengine=libaio --group_reporting "${fio_time[@]}" ;;
    bulk_sequential_writer)
        phase bulk_sequential_writer bulk_sequential_writer measure "$io_signals" io "${read_victim[@]}" -- \
            fio --name=writer --filename="$scratch/writer.dat" --size=4g --rw=write --bs=1m --direct=1 \
            --iodepth=16 --ioengine=libaio --rate="$(pick "$level" 300m 600m 1g)" "${fio_time[@]}" ;;
    sync_write_storm)
        phase sync_write_storm sync_write_storm measure "$io_signals" io "${fsync_victim[@]}" -- \
            fio --name=syncer --directory="$scratch" --size=64m --rw=randwrite --bs=4k --fsync=1 \
            --numjobs="$(pick "$level" 4 8 16)" --group_reporting "${fio_time[@]}" ;;
    writeback_storm)
        # As fast as it can: at 1.5 GB/s the flushers kept dirty pages under
        # the background threshold and it was a plain heavy writer (pilot
        # 20261115T134515Z).
        phase writeback_storm writeback_storm measure \
            '["mem.dirty_bytes","disk.write_bytes_per_s","cgroup.io_pressure_some_ratio"]' io \
            "${fsync_victim[@]}" -- \
            fio --name=writer --filename="$scratch/writer.dat" --size=12g --rw=write \
            --bs="$(pick "$level" 256k 1m 4m)" "${fio_time[@]}" ;;
    harmless_cpu)
        phase harmless_cpu - false "$cpu_signals" cpu stress-ng --cpu "$(pick "$level" 2 $((cpus / 4)) $((cpus / 2)))" $t ;;
    harmless_copy)
        [ -f "$scratch/big.dat" ] || fio --name=fill --filename="$scratch/big.dat" --size=8g --rw=write --bs=1m \
            --direct=1 >/dev/null
        phase harmless_copy - false "$io_signals" io sh -c "cp '$scratch/big.dat' '$scratch/copy.dat'; sync; rm -f '$scratch/copy.dat'" ;;
    harmless_build)
        # A release build of the agent from nothing, in a target of its own.
        phase harmless_build - false "$cpu_signals" cpu env CARGO_TARGET_DIR="$scratch/target" \
            timeout 300 cargo build --release --offline --quiet --manifest-path "$here/../agent/Cargo.toml"
        rm -rf "$scratch/target" ;;
    harmless_memory)
        phase harmless_memory - false "$mem_signals" memory \
            stress-ng --vm 1 --vm-bytes "$(( $(avail_kb) / 4 ))k" --vm-keep $t ;;
    group_memory_throttled)
        phase group_memory_throttled group_memory_throttled true '["cgroup.mem_high_events"]' memory \
            "MemoryHigh=$(pick "$level" 384M 256M 128M)" --- stress-ng --vm 1 --vm-bytes 512m --vm-keep $t ;;
    group_memory_limit)
        phase group_memory_limit group_memory_limit true '["cgroup.mem_max_events","cgroup.oom_kills"]' memory \
            "MemoryMax=$(pick "$level" 384M 256M 128M)" MemorySwapMax=0 --- \
            stress-ng --vm 1 --vm-bytes 512m --vm-keep $t ;;
    memory_hog)
        # Most of the available memory, short of swapping.
        phase memory_hog memory_hog measure "$mem_signals" memory "${mem_victim[@]}" -- \
            stress-ng --vm 1 --vm-bytes "$(( $(avail_kb) * $(pick "$level" 80 90 95) / 100 ))k" --vm-keep $t ;;
    swap_io)
        # More than is available: the host swaps (S5, run 20261030T202141Z).
        phase swap_io swap_io measure "$mem_signals" memory "${mem_victim[@]}" -- \
            stress-ng --vm 1 --vm-bytes "$(( $(avail_kb) + $(pick "$level" 512 1024 2048) * 1024 ))k" --vm-keep $t ;;
    *) echo "unknown scenario $s" >&2; exit 1 ;;
    esac
}

avail_kb() { awk '/MemAvailable/ {print $2}' /proc/meminfo; }

passes=$repeat
echo "Scenario suite $run on $host: $passes pass(es), $(echo $wanted | wc -w) scenarios each"
echo "Leave the host alone; it will not sleep until the suite ends."
run_all() {
    for pass in $(seq 1 "$passes"); do
        level=${fixed_level:-$(( (first_level + pass - 1) % 3 ))}
        echo "Pass $pass, intensity $level"
        for s in $wanted; do scenario "$s"; done
    done
}
# Keeps the host awake; the functions run in this shell.
systemd-inhibit --what=idle:sleep:handle-lid-switch --who=gungnir --why="scenario suite" \
    sleep infinity &
inhibitor=$!
trap 'kill $inhibitor 2>/dev/null; rm -rf "$scratch"' EXIT
run_all
echo "Phases recorded in $label_file, measured harm in $harm_file"
"${server[@]}" runs import "$host" - <"$label_file"
echo "Score with: gungnir-server replay $host live --since <start> --until <end>; gungnir-server compare $host"
