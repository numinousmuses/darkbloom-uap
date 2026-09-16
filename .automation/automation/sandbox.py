"""Disposable execution; only the working directory is writable on the host."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from .model_proxy import gateway

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "darkbloom-uap:local"


def module_cache():
    return Path(os.environ.get("DARKBLOOM_MODULE_CACHE", str(Path.home() / ".cache/darkbloom-agent/go-mod")))


def run(argv, cwd=None, **kwargs):
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, **kwargs)


def export(repo, sha, destination):
    destination.mkdir(parents=True)
    with tempfile.TemporaryFile() as archive:
        subprocess.run(["git", "archive", sha], cwd=repo, stdout=archive, check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(destination, filter="data")


def docker(workspace, argv, *, mounts=(), environment=(), timeout=1200, log=None):
    name = "db-agent-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "--rm", "--name", name, "--network=none",
               "--cap-drop=ALL", "--security-opt=no-new-privileges",
               "--read-only", "--pids-limit=256", "--memory=3g", "--cpus=2",
               "--user", f"{os.getuid()}:{os.getgid()}",
               "--tmpfs", "/tmp:rw,exec,nosuid,size=2g,mode=1777",
               "-v", f"{workspace}:/workspace:rw"]
    for source, target in mounts:
        command += ["-v", f"{source}:{target}:ro"]
    for name_, value in environment:
        command += ["-e", f"{name_}={value}"]
    command += [IMAGE, *argv]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
        if log:
            log.write_bytes(result.stdout + b"\n" + result.stderr)
        return result
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def snapshot_diff(base, candidate, out):
    # Keep Git metadata outside the agent's writable mount. Never load its config.
    with tempfile.TemporaryDirectory() as temp:
        index = Path(temp)
        shutil.copytree(base, index / "tree", symlinks=True)
        env = {"PATH": os.environ["PATH"], "HOME": temp,
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        run(["git", "init", "-q"], index / "tree", env=env)
        run(["git", "add", "-f", "-A"], index / "tree", env=env)
        run(["git", "-c", "user.name=Snapshot", "-c", "user.email=snapshot@localhost",
             "commit", "-qm", "Source snapshot"], index / "tree", env=env)
        env.update(GIT_DIR=str(index / "tree/.git"), GIT_WORK_TREE=str(candidate))
        run(["git", "add", "-A"], env=env)
        patch = run(["git", "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv"], env=env).stdout
        paths = run(["git", "diff", "--cached", "--name-only", "-z"], env=env).stdout
        changed = [p.decode() for p in paths.split(b"\0") if p]
        if any(p.startswith((".git", ".automation/")) or p in ("AGENTS.md", "CLAUDE.md") for p in changed):
            raise ValueError("Candidate changed execution policy or repository instructions")
        out.write_bytes(patch)
        return changed


def execute(repo, source, instruction, role, output, *, model, max_turns=40, timeout=1200):
    output.mkdir(parents=True, exist_ok=False)
    baseline, workspace = output / "baseline", output / "workspace"
    export(repo, source, baseline)
    shutil.copytree(baseline, workspace, symlinks=True)
    rendered = output / "agent"
    uap = ROOT / "node_modules/.bin/uap"
    bundle = ROOT / "bundles" / role
    run([str(uap), "validate", str(bundle), "--strict"])
    report = run([str(uap), "unpack", "--to", "claude-code", str(rendered),
                  "-b", str(bundle), "--on-unsupported", "error", "--json"])
    (output / "uap-report.json").write_bytes(report.stdout)
    (output / "task.txt").write_text(instruction)
    mounts = [(rendered, "/agent"), (output / "task.txt", "/task.txt"),
              (ROOT / "automation/model_proxy.py", "/bridge.py"), (baseline, "/baseline")]
    modcache = module_cache()
    if modcache.exists():
        mounts.append((modcache, "/gomod"))
    with tempfile.TemporaryDirectory(prefix="db-model-") as tmp:
        socket_dir = Path(tmp)
        mounts.append((socket_dir, "/model"))
        with gateway(socket_dir / "api.sock", model, max_requests=max_turns * 4):
            command = ["sh", "-c", 'mkdir -p /tmp/home; python3 /bridge.py /model/api.sock & '
                       'exec claude --bare --setting-sources "" --strict-mcp-config '
                       '--mcp-config \'{"mcpServers":{}}\' --dangerously-skip-permissions '
                       '--append-system-prompt-file /agent/CLAUDE.md --model "$1" '
                       '--max-turns "$2" --output-format json -p "$(cat /task.txt)"',
                       "agent", model, str(max_turns)]
            result = docker(workspace, command, mounts=mounts, timeout=timeout,
                            environment=[("ANTHROPIC_BASE_URL", "http://127.0.0.1:8181"),
                                         ("ANTHROPIC_API_KEY", "sandbox-no-credential"),
                                         ("GOMODCACHE", "/gomod"), ("GOPROXY", "off"),
                                         ("GOTOOLCHAIN", "local"), ("GOSUMDB", "off")],
                            log=output / "agent.log")
    if result.returncode:
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeDecodeError):
            response = {}
        reason = "The agent could not complete the task. The operator needs to inspect the execution failure."
        if response.get("subtype") == "error_max_turns":
            reason = "The task reached its turn limit before completion. Narrow the request or increase its reviewed limit."
        (output / "failure.json").write_text(json.dumps({"stage": "execution", "reason": reason}))
        raise RuntimeError(f"Agent process failed ({result.returncode}); inspect private run log")
    response = json.loads(result.stdout)
    if response.get("is_error") or response.get("subtype") != "success":
        raise RuntimeError("Agent did not complete successfully; inspect private run log")
    (output / "summary.md").write_text(response.get("result", "No summary returned."))
    changed = snapshot_diff(baseline, workspace, output / "candidate.patch")
    if role == "reviewer" and changed:
        raise ValueError("Read-only reviewer changed source files")
    return changed
