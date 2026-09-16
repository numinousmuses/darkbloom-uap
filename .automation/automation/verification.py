"""Accepted checks run in a fresh sandbox, without the agent or model connection."""

import hashlib
import json
from pathlib import Path
import tempfile
import tarfile
import subprocess

from .sandbox import docker, export, run, module_cache

RECIPES = {
    "protocol": ["go", "test", "-race", "-count=1", "./coordinator/protocol/..."],
    "responses": ["go", "test", "-race", "-count=1", "./coordinator/api/...", "./coordinator/promptcontract/..."],
    "coordinator": ["go", "test", "-race", "-count=1", "./coordinator/..."],
    "release-scripts": ["python3", "scripts/test-provider-release-resolution.py"],
}

# Metrics assertions describe application tags, not the runner's container ID.
TEST_ENV = [("GOMODCACHE", "/gomod"), ("GOPROXY", "off"), ("GOTOOLCHAIN", "local"),
            ("GOSUMDB", "off"), ("DD_ORIGIN_DETECTION_ENABLED", "false")]


def require_coverage(paths, recipe):
    prefixes = {"protocol": ("coordinator/protocol/",), "responses": ("coordinator/api/", "coordinator/promptcontract/"),
                "coordinator": ("coordinator/",), "release-scripts": ("scripts/provider-release-resolution",)}
    for path in paths:
        if path.startswith("docs/") and path.endswith(".md"):
            continue
        if path.startswith("coordinator/promptsidecar/") or not path.startswith(prefixes[recipe]):
            raise ValueError(f"The {recipe} recipe does not cover {path}")


def verify(repo, source, patch, recipe, output, timeout=1200, baseline=None):
    output.mkdir(parents=True, exist_ok=True)
    patch_bytes = Path(patch).read_bytes() if patch else b""
    if len(patch_bytes) > 5_000_000:
        raise ValueError("Candidate exceeds patch size limit")
    with tempfile.TemporaryDirectory(prefix="db-verify-") as temp:
        workspace = Path(temp) / "workspace"
        export(repo, source, workspace)
        if patch_bytes:
            run(["git", "init", "-q"], workspace)
            run(["git", "add", "-A"], workspace)
            run(["git", "apply", "--check", str(Path(patch).resolve())], workspace)
            run(["git", "apply", str(Path(patch).resolve())], workspace)
            changed = run(["git", "diff", "--name-only", "-z"], workspace).stdout
            added = run(["git", "ls-files", "--others", "--exclude-standard", "-z"], workspace).stdout
            paths = [p.decode() for p in (changed + added).split(b"\0") if p]
            if recipe == "affected":
                code = [p for p in paths if not (p.startswith("docs/") and p.endswith(".md"))]
                if not code:
                    raise ValueError("No code changes covered by the available recipes")
                if all(p.startswith("coordinator/protocol/") for p in code):
                    recipe = "protocol"
                elif all(p.startswith(("coordinator/api/", "coordinator/promptcontract/")) for p in code):
                    recipe = "responses"
                else:
                    recipe = "coordinator"
            require_coverage(paths, recipe)
        elif recipe == "affected":
            recipe = "coordinator"
        command = RECIPES[recipe]
        modcache = module_cache()
        mounts = [(modcache, "/gomod")] if modcache.exists() else []
        result = docker(workspace, command, mounts=mounts, timeout=timeout,
                        environment=TEST_ENV,
                        log=output / "verification.log")
        baseline_exit = None
        if recipe != "release-scripts" and result.returncode == 0:
            # A proposed edit cannot delete or neutralize its own regression gate.
            # Restore accepted tests, preserving newly added test files, then rerun.
            changed_tests = False
            with tempfile.TemporaryFile() as archive:
                subprocess.run(["git", "archive", baseline or source, "coordinator"], cwd=repo, stdout=archive, check=True)
                archive.seek(0)
                with tarfile.open(fileobj=archive) as tar:
                    for member in tar:
                        if member.isfile() and member.name.endswith("_test.go"):
                            target = workspace / member.name
                            if target.is_symlink() or not target.resolve().is_relative_to(workspace.resolve()):
                                raise ValueError("Candidate redirected an accepted test path")
                            accepted = tar.extractfile(member).read()
                            if not target.exists() or target.read_bytes() != accepted:
                                changed_tests = True
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(accepted)
            baseline_exit = 0
            if changed_tests:
                preserved = docker(workspace, command, mounts=mounts, timeout=timeout,
                                   environment=TEST_ENV,
                                   log=output / "accepted-tests.log")
                baseline_exit = preserved.returncode
    receipt = {"schema": 1, "source": source, "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
               "recipe": recipe, "command": command, "exit_code": result.returncode,
               "accepted_tests_exit_code": baseline_exit,
               "conclusion": "passed" if result.returncode == 0 and baseline_exit in (None, 0) else "failed"}
    (output / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
