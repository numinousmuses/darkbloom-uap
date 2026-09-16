"""Publish only an independently checked patch as a signed, draft PR."""

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .admission import current
from .github import api
from .sandbox import export, run


def candidate_pr(repo_path, request, patch, receipt_path, summary_path=None):
    receipt = json.loads(receipt_path.read_text())
    data = patch.read_bytes()
    if receipt["conclusion"] != "passed" or receipt["source"] != request["source"]:
        raise ValueError("Candidate has no matching successful verification")
    if receipt["patch_sha256"] != hashlib.sha256(data).hexdigest():
        raise ValueError("Candidate changed after verification")
    if not data:
        return None
    if not current(request):
        raise ValueError("Source PR has changed; rerun before publishing")
    repo, source = request["repo"], request["source"]
    branch = "automation/task-" + request["request_id"]
    existing = api(f"repos/{repo}/pulls?state=all&head={repo.split('/')[0]}:{branch}")
    if existing:
        return existing[0]
    with tempfile.TemporaryDirectory(prefix="db-publish-") as tmp:
        tree = Path(tmp) / "tree"
        export(repo_path, source, tree)
        run(["git", "init", "-q"], tree)
        run(["git", "add", "-A"], tree)
        run(["git", "apply", "--check", "--index", str(patch.resolve())], tree)
        run(["git", "apply", str(patch.resolve())], tree)
        changed = run(["git", "diff", "--name-only", "-z"], tree).stdout.split(b"\0")
        added = run(["git", "ls-files", "--others", "--exclude-standard", "-z"], tree).stdout.split(b"\0")
        paths = sorted({p.decode() for p in changed + added if p})
        if len(paths) > 40:
            raise ValueError("Candidate exceeds publication file limit")
        additions, deletions = [], []
        for name in paths:
            if name.startswith((".git", ".automation/")) or name in ("AGENTS.md", "CLAUDE.md"):
                raise ValueError("Candidate changes execution policy")
            path = tree / name
            if path.is_symlink() or not path.resolve().is_relative_to(tree.resolve()):
                raise ValueError("Candidate contains a symlink or escaping path")
            if path.exists():
                if path.stat().st_mode & 0o111:
                    raise ValueError("Signed API publication cannot preserve executable file mode")
                additions.append({"path": name, "contents": base64.b64encode(path.read_bytes()).decode()})
            else:
                deletions.append({"path": name})
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "accepted-index")}
        run(["git", "read-tree", source], repo_path, env=env)
        run(["git", "apply", "--cached", str(patch.resolve())], repo_path, env=env)
        expected_tree = run(["git", "write-tree"], repo_path, env=env).stdout.decode().strip()
    refs = api(f"repos/{repo}/git/matching-refs/heads/{branch}")
    if not refs:
        api(f"repos/{repo}/git/refs", {"ref": "refs/heads/" + branch, "sha": source})
    elif refs[0]["object"]["sha"] != source:
        prior = api(f"repos/{repo}/commits/{refs[0]['object']['sha']}")
        if (prior["commit"]["tree"]["sha"] != expected_tree or
                [parent["sha"] for parent in prior["parents"]] != [source] or
                not prior["commit"]["verification"]["verified"]):
            raise RuntimeError("Existing branch differs from the verified candidate")
    issue = api(f"repos/{repo}/issues/{request['thread']}")
    title = issue["title"].removeprefix("[bug] ")[:150]
    mutation = """mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid signature{isValid}}}}"""
    if not refs or refs[0]["object"]["sha"] == source:
        result = api("graphql", {"query": mutation, "variables": {"input": {
            "branch": {"repositoryNameWithOwner": repo, "branchName": branch}, "expectedHeadOid": source,
            "message": {"headline": title}, "fileChanges": {"additions": additions, "deletions": deletions}}}})
        if result.get("errors"):
            raise RuntimeError("Signed commit publication failed")
        commit = result["data"]["createCommitOnBranch"]["commit"]
        if not commit.get("signature", {}).get("isValid"):
            raise RuntimeError("Candidate commit signature is not verified")
        actual = api(f"repos/{repo}/git/commits/{commit['oid']}")
        if actual["tree"]["sha"] != expected_tree:
            raise RuntimeError("Published tree differs from verified patch")
    summary = summary_path.read_text()[:8000] if summary_path and summary_path.exists() else "A candidate is ready for review."
    summary = summary.replace("<!--", "&lt;!--").replace("@", "@\u200b")
    body = (f"Addresses #{request['thread']}.\n\n" + summary + "\n\n"
            "Changed files:\n" + "\n".join(f"- `{p}`" for p in paths) + "\n\n"
            f"Independent verification: `{' '.join(receipt['command'])}` passed against the source revision plus this exact patch. "
            "The published commit receives its own CI run. Review the behavior and test coverage before merging.\n")
    default = api(f"repos/{repo}")["default_branch"]
    pr = api(f"repos/{repo}/pulls", {"title": title, "body": body, "head": branch, "base": default, "draft": True})
    api(f"repos/{repo}/issues/{pr['number']}/labels", {"labels": ["trigger:" + request["trigger"]]})
    return pr
