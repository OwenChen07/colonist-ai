"""scripts/vm/gcp_start_spot.sh with a fake gcloud: a stopped spot VM is
started at once, and one still STOPPING (distributed.py calls the script the
moment a host drops out) is started by a background waiter once stopped."""

import os
import stat
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vm" / "gcp_start_spot.sh"

FAKE = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/calls"
case "$*" in
  "config configurations describe"*) exit 1 ;;
  "compute instances list"*status=RUNNING*) cat "$FAKE_DIR/running" ;;
  "compute instances list"*) cat "$FAKE_DIR/list" ;;
  "compute instances describe"*)
    n=$(cat "$FAKE_DIR/polls" 2>/dev/null || echo 0); echo $((n + 1)) > "$FAKE_DIR/polls"
    sed -n "$((n + 1))p" "$FAKE_DIR/statuses" ;;
esac
"""


def _run(tmp_path, listing, statuses=(), args=(), running=""):
    gcloud = tmp_path / "bin" / "gcloud"
    gcloud.parent.mkdir()
    gcloud.write_text(FAKE)
    gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "list").write_text(listing)
    (tmp_path / "running").write_text(running)
    (tmp_path / "statuses").write_text("".join(s + "\n" for s in statuses))
    env = dict(
        os.environ,
        PATH=f"{gcloud.parent}:{os.environ['PATH']}",
        FAKE_DIR=str(tmp_path),
        SPOT_LOG=str(tmp_path / "spot.log"),
        GPU_LOG=str(tmp_path / "gpu.log"),
        STOP_POLL_WAIT="0",
        STOP_POLLS="5",
        RUN_POLLS="5",
    )
    out = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def _calls(tmp_path, wait_for=None):
    deadline = time.time() + 10
    while True:
        calls = (tmp_path / "calls").read_text().splitlines()
        if (
            wait_for is None
            or any(wait_for in c for c in calls)
            or time.time() > deadline
        ):
            return calls
        time.sleep(0.05)


def test_terminated_vm_is_started_at_once(tmp_path):
    out = _run(tmp_path, "selfplay-spot-7\tus-east1-c\tTERMINATED\n")
    assert "starting selfplay-spot-7" in out
    starts = [c for c in _calls(tmp_path) if "instances start" in c]
    assert starts == [
        "compute instances start selfplay-spot-7 --zone us-east1-c --async --quiet"
    ]


def test_stopping_vm_is_started_once_stopped(tmp_path):
    out = _run(
        tmp_path,
        "selfplay-spot-7\tus-east1-c\tSTOPPING\n",
        ["STOPPING", "STOPPING", "TERMINATED"],
    )
    assert "is stopping" in out
    calls = _calls(tmp_path, "instances start")
    starts = [c for c in calls if "instances start" in c]
    assert starts == [
        "compute instances start selfplay-spot-7 --zone us-east1-c --async --quiet"
    ]
    assert sum("instances describe" in c for c in calls) == 3
    assert "after its stop" in (tmp_path / "spot.log").read_text()


def test_labelled_vm_outside_the_fleet_is_started_once_stopped(tmp_path):
    # a research VM with autostart=yes (E83's e54-vm, Oct 21)
    _run(tmp_path, "e54-vm\tus-central1-a\tSTOPPING\n", ["STOPPING", "TERMINATED"])
    calls = _calls(tmp_path, "instances start")
    assert any("labels.autostart=yes" in c for c in calls if "instances list" in c)
    starts = [c for c in calls if "instances start" in c]
    assert starts == [
        "compute instances start e54-vm --zone us-central1-a --async --quiet"
    ]


def test_stopping_vm_that_runs_again_is_left_alone(tmp_path):
    _run(tmp_path, "selfplay-spot-7\tus-east1-c\tSTOPPING\n", ["RUNNING"])
    _calls(tmp_path, "instances describe")
    time.sleep(0.3)
    calls = _calls(tmp_path)
    assert not any("instances start" in c for c in calls)


def test_lost_host_still_running_is_started_once_stopped(tmp_path):
    # distributed.py names the lost host before GCP marks its VM STOPPING
    out = _run(
        tmp_path,
        "",
        ["RUNNING", "RUNNING", "STOPPING", "TERMINATED"],
        args=["owen@10.142.0.8"],
        running="selfplay-spot-9\tus-east1-d\n",
    )
    assert "selfplay-spot-9 (us-east1-d) dropped out while RUNNING" in out
    calls = _calls(tmp_path, "instances start")
    assert any("networkIP=10.142.0.8" in c for c in calls)
    starts = [c for c in calls if "instances start" in c]
    assert starts == [
        "compute instances start selfplay-spot-9 --zone us-east1-d --async --quiet"
    ]
    assert sum("instances describe" in c for c in calls) == 4


def test_lost_host_that_stays_running_is_left_alone(tmp_path):
    _run(
        tmp_path,
        "",
        ["RUNNING"] * 5,
        args=["owen@10.142.0.8"],
        running="selfplay-spot-9\tus-east1-d\n",
    )
    calls = _calls(tmp_path)
    for _ in range(200):
        if sum("instances describe" in c for c in calls) == 5:
            break
        time.sleep(0.05)
        calls = _calls(tmp_path)
    assert sum("instances describe" in c for c in calls) == 5  # RUN_POLLS
    assert not any("instances start" in c for c in calls)


def test_before_round_call_does_not_look_for_running_vms(tmp_path):
    _run(tmp_path, "")
    assert not any("status=RUNNING" in c for c in _calls(tmp_path))
