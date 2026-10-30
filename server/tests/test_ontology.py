import shutil

import pytest

from gungnir_core import ontology, workload
from gungnir_core.knowledge import KnowledgeError


@pytest.fixture(scope="module")
def onto():
    return ontology.load()


def broken(tmp_path, extra: str):
    """The shipped ontology with `extra` Turtle appended."""
    file = tmp_path / "gungnir.ttl"
    file.write_text(ontology.path().read_text() + extra)
    return file


def test_shipped_ontology_loads(onto):
    for category in ontology.CATEGORIES:
        assert onto.names(category), category
    disk = onto.signals["disk.write_bytes_per_s"]
    assert (disk.resource, disk.entity, disk.kind, disk.impact) == ("disk", "block_device", "rate", False)
    assert onto.signals["cgroup.io_pressure_some_ratio"].impact
    assert onto.causes["device_errors"].culprit is None
    assert onto.causes["swap_io"].resources == {"memory", "disk"}
    assert onto.roles["culprit"].kinds == {"control_group", "process"}


def test_workload_classes_are_the_ontology_ones(onto):
    assert set(workload.CLASSES) == onto.classes


def test_knowledge_files_name_only_what_the_ontology_has(onto):
    checked, problems = ontology.references(onto)
    assert checked > 0
    assert problems == []


@pytest.mark.parametrize(
    ("event", "source", "message"),
    [
        # Formats from the kernel sources cited in the ontology.
        (
            "block_io_error",
            "kernel",
            "I/O error, dev nvme0n1, sector 4096 op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2",
        ),
        (
            "nvme_timeout",
            "kernel",
            "nvme nvme0: I/O tag 12 (300c) opcode 0x2 (Read) QID 3 timeout, aborting req_op:READ(0)",
        ),
        (
            "nvme_timeout",
            "kernel",
            "nvme nvme0: I/O tag 12 (300c) opcode 0x2 (Read) QID 3 timeout, reset controller",
        ),
        (
            "btrfs_device_errors",
            "kernel",
            "BTRFS error (device nvme0n1p2): bdev /dev/nvme0n1p2 errs: wr 0, rd 3, flush 0, corrupt 0, gen 0",
        ),
        ("hung_task", "kernel", "INFO: task kworker/u16:2:1234 blocked for more than 122 seconds."),
        (
            "oom_kill",
            "kernel",
            "Out of memory: Killed process 4321 (stress-ng) total-vm:8000kB, anon-rss:4000kB",
        ),
    ],
)
def test_event_patterns_match_kernel_messages(onto, event, source, message):
    matched = [e.name for e in onto.events.values() if e.matches(source, None, message)]
    assert matched == [event]


def test_event_matching_needs_source_and_message_id(onto):
    failed = onto.events["unit_failed"]
    assert failed.matches(
        "systemd", "d9b373ed55a64feb8242e02dbe79a49c", "backup.service: Failed with result 'exit-code'."
    )
    assert not failed.matches("systemd", "fe6faa94e7774663a0da52717891d8ef", "anything")
    assert not any(
        e.matches("kernel", None, "nvme nvme0: 8/0/0 default/read/poll queues") for e in onto.events.values()
    )


@pytest.mark.parametrize(
    ("comm", "state", "signs"),
    [
        # Names and states seen in the laptop's process snapshots.
        ("kworker/u73:2+flush-btrfs-1", "D", {"writeback_flusher", "waiting_on_io"}),
        ("kworker/u73:3-flush-btrfs-1", "I", set()),
        ("btrfs-transaction", "D", {"btrfs_transaction", "waiting_on_io"}),
        ("btrfs-cleaner", "S", {"btrfs_cleaner"}),
        ("kswapd0", "R", {"kswapd"}),
        ("jbd2/sda1-8", "S", {"jbd2"}),
        ("firefox", "S", set()),
    ],
)
def test_process_signs(onto, comm, state, signs):
    assert {s.name for s in onto.signs.values() if s.matches(comm, state)} == signs


@pytest.mark.parametrize(
    ("extra", "complaint"),
    [
        (
            'signal:x.y a g:Signal ; g:observedOn kind:host ; g:unit "1" ; g:signalKind "ratio" ;'
            ' g:source "s" ; g:grade "documented" .',
            "signal:x.y: no measures",
        ),
        (
            'signal:x.y a g:Signal ; g:measures kind:host ; g:observedOn kind:host ; g:unit "1" ;'
            ' g:signalKind "ratio" ; g:source "s" ; g:grade "documented" .',
            "measures must name a Resource",
        ),
        (
            'signal:x.y a g:Signal ; g:measure res:cpu ; g:observedOn kind:host ; g:unit "1" ;'
            ' g:signalKind "ratio" ; g:source "s" ; g:grade "documented" .',
            "ontology#measure is used but not declared",
        ),
        (
            'signal:x.y a g:Signal ; g:measures res:cpu ; g:observedOn kind:host ; g:unit "1" ;'
            ' g:signalKind "ratio" ; g:grade "documented" .',
            "signal:x.y: no provenance source",
        ),
        (
            'signal:x.y a g:Signal ; g:measures res:cpu ; g:observedOn kind:host ; g:unit "1" ;'
            ' g:signalKind "gauge" ; g:source "s" ; g:grade "documented" .',
            "signalKind must be one of",
        ),
        (
            'event:x a g:EventType ; g:emittedBy "kernel" ; g:messagePattern "(" ;'
            ' g:source "s" ; g:grade "documented" .',
            "event:x: messagePattern",
        ),
        (
            'cause:x a g:Signal ; g:source "s" ; g:grade "documented" .',
            "is a Signal but not in the namespace",
        ),
        (
            "cause:x a g:RootCause ; g:concerns res:disk ; g:culpritKind kind:block_device ;"
            ' g:source "s" ; g:grade "documented" .',
            "the culprit role cannot bind to block_device",
        ),
    ],
)
def test_closed_world_checks(tmp_path, extra, complaint):
    with pytest.raises(KnowledgeError, match=complaint):
        ontology.load(broken(tmp_path, extra))


def hermit():
    owlready2 = pytest.importorskip("owlready2")
    if shutil.which(owlready2.JAVA_EXE) is None:
        pytest.skip("no Java for HermiT")


def test_hermit_finds_the_ontology_consistent_and_classifies_causes():
    hermit()
    classified = ontology.consistency()
    assert "swap_io" in classified["DiskCause"]
    assert "swap_io" in classified["MemoryCause"]
    assert classified["FilesystemCause"] == ["filesystem_filled", "filesystem_full"]


def test_hermit_rejects_a_signal_that_measures_two_resources(tmp_path):
    hermit()
    extra = (
        'signal:x.y a g:Signal ; g:measures res:cpu , res:memory ; g:observedOn kind:host ; g:unit "1" ;'
        ' g:signalKind "ratio" ; g:source "s" ; g:grade "documented" .'
    )
    with pytest.raises(KnowledgeError, match="inconsistent"):
        ontology.consistency(broken(tmp_path, extra))
