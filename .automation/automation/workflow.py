"""Small Actions entrypoints, keeping workflow expressions out of task content."""

import json
import os
import re
import subprocess
from pathlib import Path
import sys

from .admission import admit
from .github import api, pages
from .publish import candidate_pr
from .sandbox import ROOT
from . import github
from .admission import current


def prepare():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    # Run ID survives reruns and distinguishes actual scheduled occurrences.
    if os.environ["GITHUB_EVENT_NAME"] in ("schedule", "workflow_dispatch"):
        event.setdefault("inputs", {})["occurrence"] = os.environ["GITHUB_RUN_ID"]
    request = admit(event, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REPOSITORY"],
                    json.loads((ROOT / "policy.json").read_text()))
    output = Path(os.environ["GITHUB_WORKSPACE"]) / "request.json"
    output.write_text(json.dumps(request, indent=2) + "\n")
    values = {"accepted": "false"}
    if request:
        recipe = event.get("inputs", {}).get("recipe") or ("affected" if request["role"] == "implementer" else "coordinator")
        blocked = ""
        if request["is_pr"]:
            files = pages(f"repos/{request['repo']}/pulls/{request['thread']}/files")
            paths = [p for f in files for p in (f["filename"], f.get("previous_filename")) if p]
            executable = [p for p in paths if not p.startswith("docs/") and not p.endswith(".md")]
            if any(p.startswith(("provider-swift/", "libs/", "console-ui/", "admin-ui/", "coordinator/promptsidecar/")) for p in paths):
                blocked = "This change needs native provider, sidecar, or frontend checks before it is ready to merge."
            elif executable and all(p.startswith("coordinator/protocol/") for p in executable):
                recipe = "protocol"
            elif executable and all(p.startswith(("coordinator/api/", "coordinator/promptcontract/")) for p in executable):
                recipe = "responses"
            elif any(not p.startswith("coordinator/") for p in executable):
                blocked = "This change needs a verification recipe for the affected files."
        elif request["trigger"] == "schedule":
            recipe = json.loads((ROOT / "policy.json").read_text())["schedule_recipe"]
        request["recipe"], request["blocked"] = recipe, blocked
        output.write_text(json.dumps(request, indent=2) + "\n")
        values = {"accepted": "true", "source": request["source"], "thread": request["thread"],
                  "role": request["role"], "recipe": recipe, "blocked": "true" if blocked else "false"}
        values["policy_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        request["policy_revision"] = values["policy_sha"]
        output.write_text(json.dumps(request, indent=2) + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def publish():
    request = json.loads(Path("request.json").read_text())
    patch = Path("result/candidate.patch")
    if request["role"] == "implementer" and patch.exists() and patch.stat().st_size:
        pr = candidate_pr(Path("candidate"), request, patch, Path("result/verification.json"), Path("result/summary.md"))
        if pr:
            Path("result/pr.json").write_text(json.dumps(pr))
            print(pr["html_url"])
            # Actions-created PRs do not automatically trigger another workflow.
            default = api(f"repos/{request['repo']}")["default_branch"]
            api(f"repos/{request['repo']}/actions/workflows/darkbloom-work.yml/dispatches",
                {"ref": default, "inputs": {"issue": str(pr["number"]), "mode": "review", "recipe": json.loads(Path("result/verification.json").read_text())["recipe"]}})


def final_state():
    request = json.loads(Path("request.json").read_text())
    status, checks = "failed", []
    summary = "The run stopped before all checks completed. No passing result is being claimed."
    next_step = "Inspect the failed step in the linked run, then request a retry."
    if Path("result/failure.json").exists():
        summary = json.loads(Path("result/failure.json").read_text())["reason"]
    if Path("result/verification.json").exists():
        receipt = json.loads(Path("result/verification.json").read_text())
        if receipt["source"] != request["source"]:
            raise ValueError("Verification source mismatch")
        checks = ["`" + " ".join(receipt["command"]) + "`: " + receipt["conclusion"]]
        if receipt["conclusion"] == "failed":
            summary = "The independent check failed. The candidate has not been published as a verified change."
            failures = []
            for name in ("verification.log", "accepted-tests.log"):
                log = Path("result") / name
                if log.exists():
                    failures.extend(re.findall(r"^--- FAIL: ([\w/]+)", log.read_text(errors="replace"), re.MULTILINE))
            if failures:
                summary += "\n\nFailing tests: " + ", ".join(f"`{name}`" for name in dict.fromkeys(failures))[:1200] + "."
            findings = Path("result/summary.md")
            if findings.exists():
                summary += "\n\n<details><summary>Agent findings, pending verification</summary>\n\n" + findings.read_text()[:5000].replace("@", "@\u200b") + "\n\n</details>"
    if request.get("blocked"):
        status, summary = "blocked", request["blocked"]
        next_step = "Add the required verification environment before merging."
    elif os.environ.get("WORK_RESULT") == "success" and os.environ.get("JOB_STATUS", "success") == "success":
        receipt = json.loads(Path("result/verification.json").read_text())
        if receipt["source"] != request["source"] or receipt["conclusion"] != "passed":
            raise ValueError("Verification receipt does not match this request")
        status = "passed"
        checks = ["`" + " ".join(receipt["command"]) + "`: passed"]
        summary = Path("result/summary.md").read_text()[:5000].replace("@", "@\u200b")
        if request["role"] == "reviewer":
            summary = "Agent assessment follows. Independently rerun checks are listed below.\n\n" + summary
        next_step = "Review the findings and diff. Passing tests do not replace a maintainer's merge decision."
        if Path("result/pr.json").exists():
            pr = json.loads(Path("result/pr.json").read_text())
            next_step = f"Review [the draft change]({pr['html_url']}) and its new CI run."
    if not current(request):
        status = "stale"
        summary = "The PR changed while this run was in progress. Its result does not verify the new revision."
        next_step = "Wait for the run on the current revision."
    run_id = os.environ["GITHUB_RUN_ID"]
    return {**request, "trigger": request.get("origin", request["trigger"]), "status": status, "summary": summary, "checks": checks, "next": next_step,
            "order": int(run_id), "run_url": f"https://github.com/{request['repo']}/actions/runs/{run_id}"}


def finish():
    state = final_state()
    comment = github.upsert_status(state["repo"], state["thread"], state, "github-actions[bot]")
    if github.metadata(comment["body"]).get("order", -1) > state["order"]:
        Path("final-state.json").write_text(json.dumps({**state, "superseded": True}))
        print("A newer run owns the comment, check, and Project card.")
        return
    trigger = github.metadata(comment["body"])["trigger"]
    github.api(f"repos/{state['repo']}/issues/{state['thread']}/labels", {"labels": ["trigger:" + trigger]})
    Path("final-state.json").write_text(json.dumps(state))
    print(comment["html_url"])
    commit_status(state, "success" if state["status"] == "passed" else "failure")


def commit_status(state, status):
    # A fix receipt covers a new patch, not the original PR head. Its published
    # draft receives a separate read-only verification run before getting green.
    if state["is_pr"] and state.get("role") == "reviewer":
        api(f"repos/{state['repo']}/statuses/{state['head']}", {
            "state": status, "context": "Darkbloom / verification", "target_url": state["run_url"],
            "description": "Checks passed; review required" if status == "success" else "Verification incomplete or failed"})


def project():
    if not os.environ.get("GH_TOKEN"):
        raise RuntimeError("Project credential is not configured; comment publication is independent")
    state = json.loads(Path("final-state.json").read_text())
    if state.get("superseded"):
        return
    policy = json.loads((ROOT / "policy.json").read_text())
    github.project(state["repo"], state["thread"], state["status"], policy["project_owner"], policy["project_number"])


def start_project():
    request = json.loads(Path("request.json").read_text())
    policy = json.loads((ROOT / "policy.json").read_text())
    github.project(request["repo"], request["thread"], "working", policy["project_owner"], policy["project_number"])


if __name__ == "__main__":
    {"prepare": prepare, "publish": publish, "finish": finish, "project": project,
     "start-project": start_project}[sys.argv[1]]()
