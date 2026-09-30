"""scripts/docker-disk-textfile.sh against a fake docker CLI."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.profile import get_profile

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "docker-disk-textfile.sh"

_FAKE_DOCKER = """#!/usr/bin/env bash
case "$1 $2" in
  "system df")
    printf 'Images\\t58\\t22.96GB\\t18.06GB (78%%)\\n'
    printf 'Containers\\t20\\t11.2MB\\t0B (0%%)\\n'
    printf 'Local Volumes\\t12\\t412.3MB\\t69.1MB (16%%)\\n'
    printf 'Build Cache\\t3\\t1.02kB\\t1.02kB\\n'
    ;;
  "images -f") printf 'a\\nb\\nc\\n' ;;
  "images --format")
    for i in $(seq 18); do echo acme/platform-service; done
    echo acme/frontend; echo '<none>'; echo '<none>'
    ;;
esac
"""


def _parse(text: str) -> dict[str, float]:
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, value = line.rsplit(" ", 1)
            out[key] = float(value)
    return out


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash"
)
def test_script_exports_docker_disk_metrics(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
    textfile_dir = tmp_path / "textfile"
    env = {
        **os.environ,
        "DOCKER_BIN": str(fake),
        "TEXTFILE_DIR": str(textfile_dir),
        "DOCKER_ROOT": str(tmp_path / "no-docker-root"),
    }
    subprocess.run(["bash", str(_SCRIPT)], env=env, check=True)

    metrics = _parse((textfile_dir / "docker_disk.prom").read_text())
    assert metrics['docker_disk_bytes{type="images"}'] == 22.96e9
    assert metrics['docker_disk_reclaimable_bytes{type="images"}'] == 18.06e9
    assert metrics['docker_disk_bytes{type="local_volumes"}'] == 412.3e6
    assert metrics['docker_disk_bytes{type="build_cache"}'] == 1020
    assert metrics['docker_disk_objects{type="images"}'] == 58
    assert metrics["docker_images_dangling"] == 3
    assert metrics["docker_image_tags_max_per_repo"] == 18
    assert metrics["docker_container_logs_bytes"] == 0
    assert list(textfile_dir.iterdir()) == [textfile_dir / "docker_disk.prom"]


def test_disk_alerts_collect_docker_metrics_from_script():
    metrics = get_profile().metrics
    names = metrics.alert_metrics["HostDiskFillPredicted"]
    assert "docker_images_reclaimable_bytes" in names
    for name in names:
        if name.startswith("docker_"):
            query = metrics.render(name, service="host")
            assert query and query.startswith("max(docker_")
