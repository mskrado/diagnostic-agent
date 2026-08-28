"""Merge upstream releases into a client fork."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.fork.boundary import CLIENT_DIR
from app.fork.drift import DriftCheckError, find_upstream_drift, read_upstream_version


def _run(cmd: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        check=check,
    )


def _remote_exists(repo_root: Path, remote: str) -> bool:
    try:
        proc = _run(["git", "remote"], repo_root, check=True)
        return remote in proc.stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False


def _changelog_between(repo_root: Path, old: str, new_ref: str) -> str:
    if not old:
        return f"(no prior upstream version recorded — merging {new_ref})"
    try:
        proc = _run(
            ["git", "log", f"v{old}..{new_ref}", "--oneline", "--no-decorate"],
            repo_root,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        proc = _run(
            ["git", "log", f"{old}..{new_ref}", "--oneline", "--no-decorate"],
            repo_root,
            check=False,
        )
        return proc.stdout.strip() or f"(no commits between {old} and {new_ref})"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(could not read changelog: {exc})"


def _corpus_diff_summary(repo_root: Path, old: str, new_ref: str) -> list[str]:
    paths = ("runbooks/", "app/profile/presets/", "eval/")
    notes: list[str] = []
    for prefix in paths:
        try:
            proc = _run(
                ["git", "diff", "--stat", f"v{old}..{new_ref}", "--", prefix],
                repo_root,
                check=False,
            )
            if proc.stdout.strip():
                notes.append(f"{prefix}:\n{proc.stdout.strip()}")
        except (OSError, subprocess.SubprocessError):
            continue
    return notes


def run_upgrade(
    *,
    repo_root: Path,
    target: str = "",
    remote: str = "upstream",
    client_dir: Path | None = None,
    from_pack: Path | None = None,
    skip_drift_check: bool = False,
    dry_run: bool = False,
    fetch_remote: bool = True,
) -> int:
    """Fetch and merge an upstream release tag into the current branch."""
    repo_root = repo_root.resolve()
    client_dir = client_dir or (repo_root / CLIENT_DIR)
    current = read_upstream_version(client_dir)

    if not skip_drift_check:
        try:
            drift = find_upstream_drift(repo_root)
        except DriftCheckError as exc:
            print(
                f"ERROR: could not verify fork drift: {exc}\n"
                "Re-run inside the fork's git checkout, or pass --skip-drift-check "
                "to upgrade without the safety net.",
                file=sys.stderr,
            )
            return 1
        if drift:
            print(
                "ERROR: upstream-owned paths were modified locally. Revert them "
                "before upgrading (client config belongs under client/):\n  "
                + "\n  ".join(drift[:20]),
                file=sys.stderr,
            )
            if len(drift) > 20:
                print(f"  ... and {len(drift) - 20} more", file=sys.stderr)
            return 1

    if from_pack:
        return _upgrade_from_pack(
            repo_root, from_pack, client_dir, current, dry_run=dry_run
        )

    # Offline packs have already loaded the tags into the local repo, so an
    # air-gapped host must not be forced through a network fetch here.
    if fetch_remote:
        if not _remote_exists(repo_root, remote):
            print(
                f"ERROR: git remote '{remote}' not found. Add it:\n"
                f"  git remote add {remote} https://github.com/mskrado/diagnostic-agent.git",
                file=sys.stderr,
            )
            return 1

        try:
            fetch = _run(["git", "fetch", remote, "--tags"], repo_root, check=False)
            if fetch.returncode != 0:
                print(fetch.stderr or fetch.stdout, file=sys.stderr)
                return 1
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"fetch failed: {exc}", file=sys.stderr)
            return 1

    merge_ref = target.strip()
    if not merge_ref and not fetch_remote:
        print("ERROR: no target ref resolved from the offline pack", file=sys.stderr)
        return 1
    if not merge_ref:
        try:
            proc = _run(
                ["git", "describe", f"{remote}/main", "--tags", "--abbrev=0"],
                repo_root,
                check=False,
            )
            merge_ref = proc.stdout.strip() or f"{remote}/main"
        except (OSError, subprocess.SubprocessError):
            merge_ref = f"{remote}/main"

    print(f"Upstream version on record: {current or '(none)'}")
    print(f"Merging: {merge_ref}")
    print("\nChangelog:")
    print(_changelog_between(repo_root, current, merge_ref))

    if current:
        corpus = _corpus_diff_summary(repo_root, current, merge_ref)
        if corpus:
            print("\nShipped corpus changes (review your client/workspace/runbooks/):")
            for block in corpus:
                print(block)

    if dry_run:
        print("\nDRY-RUN: would merge and update client/.upstream-version")
        return 0

    try:
        merge = _run(["git", "merge", "--no-edit", merge_ref], repo_root, check=False)
        if merge.returncode != 0:
            print(merge.stdout)
            print(merge.stderr, file=sys.stderr)
            print(
                "\nMerge failed — resolve conflicts (only upstream paths should "
                "conflict; never edit upstream files in client/)",
                file=sys.stderr,
            )
            return merge.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"merge failed: {exc}", file=sys.stderr)
        return 1

    new_version = merge_ref.lstrip("v")
    if new_version.startswith(remote + "/"):
        try:
            proc = _run(["git", "describe", "--tags", "--abbrev=0"], repo_root)
            new_version = proc.stdout.strip().lstrip("v")
        except (OSError, subprocess.SubprocessError):
            new_version = "unknown"

    marker = client_dir / ".upstream-version"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{new_version}\n", encoding="utf-8")
    print(f"\nUpgrade OK — client/.upstream-version -> {new_version}")
    print("Rebuild the agent image: ./client/scripts/start.sh")
    return 0


def _upgrade_from_pack(
    repo_root: Path,
    pack_dir: Path,
    client_dir: Path,
    current: str,
    *,
    dry_run: bool = False,
) -> int:
    pack_dir = pack_dir.resolve()
    bundle = next(pack_dir.glob("*.bundle"), None)
    if not bundle:
        print(f"ERROR: no .bundle file in {pack_dir}", file=sys.stderr)
        return 1

    print(f"Offline pack: {bundle.name}")
    print(f"Current upstream version: {current or '(none)'}")

    if dry_run:
        print("DRY-RUN: would fetch bundle and merge tagged release")
        return 0

    try:
        fetch = _run(
            ["git", "fetch", str(bundle), "refs/tags/*:refs/tags/*"],
            repo_root,
            check=False,
        )
        if fetch.returncode != 0:
            print(fetch.stderr or fetch.stdout, file=sys.stderr)
            return 1
        tags = _run(["git", "tag", "--sort=-v:refname"], repo_root, check=False)
        latest = tags.stdout.splitlines()[0] if tags.stdout.strip() else ""
        if not latest:
            print("ERROR: bundle contained no tags", file=sys.stderr)
            return 1
        # Drift was already checked by the caller; re-running it would only
        # repeat the work, and fetch_remote=False keeps this host offline.
        return run_upgrade(
            repo_root=repo_root,
            target=latest,
            client_dir=client_dir,
            skip_drift_check=True,
            dry_run=False,
            fetch_remote=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"offline upgrade failed: {exc}", file=sys.stderr)
        return 1
