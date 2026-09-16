import argparse
import json
import os
from pathlib import Path
import sys
import time

from . import admission, github
from .sandbox import ROOT, execute, run
from .verification import RECIPES, verify


def main():
    parser = argparse.ArgumentParser(description="Run and report Darkbloom repository work")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("admit")
    p.add_argument("--event", type=Path, required=True)
    p.add_argument("--event-name", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("execute")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--repo-path", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", default=os.environ.get("AGENT_MODEL", "claude-opus-4-8"))
    p = sub.add_parser("verify")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--repo-path", type=Path, required=True)
    p.add_argument("--patch", type=Path)
    p.add_argument("--recipe", choices=RECIPES, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--status", choices=github.STATES, required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--next", default="")
    p.add_argument("--checks", type=Path)
    p.add_argument("--author", default="github-actions[bot]")
    p.add_argument("--project", action="store_true")
    args = parser.parse_args()
    policy = json.loads((ROOT / "policy.json").read_text())
    if args.command == "admit":
        request = admission.admit(json.loads(args.event.read_text()), args.event_name, args.repo, policy)
        args.out.write_text(json.dumps(request, indent=2) + "\n")
        print("accepted" if request else "ignored")
        return 0
    request = json.loads(args.request.read_text())
    if args.command == "execute":
        instruction = request["instruction"]
        if request["is_pr"]:
            diff = run(["git", "diff", "--no-ext-diff", "--no-textconv", request["base"], request["head"]], args.repo_path).stdout.decode()
            instruction += "\n\nChange under review:\n" + diff[:120000]
        changed = execute(args.repo_path.resolve(), request["source"], instruction, request["role"],
                          args.out.resolve(), model=args.model, max_turns=policy["max_turns"], timeout=policy["max_seconds"])
        print(json.dumps({"changed": changed, "request_id": request["request_id"]}))
        return 0
    if args.command == "verify":
        result = verify(args.repo_path.resolve(), request["source"], args.patch, args.recipe, args.out.resolve(), policy["max_seconds"], baseline=request["base"])
        print(json.dumps(result))
        return 0 if result["conclusion"] == "passed" else 1
    checks = []
    if args.checks:
        receipt = json.loads(args.checks.read_text())
        if receipt["source"] != request["source"]:
            raise ValueError("Verification source does not match request")
        checks = ["`" + " ".join(receipt["command"]) + "`: " + receipt["conclusion"]]
        if args.status == "passed" and receipt["conclusion"] != "passed":
            raise ValueError("Cannot publish passing status from failed verification")
    elif args.status == "passed":
        raise ValueError("Passing status requires a verification receipt")
    status = args.status if admission.current(request) else "stale"
    run_id = os.environ.get("GITHUB_RUN_ID")
    state = {**request, "trigger": request.get("origin", request["trigger"]), "status": status, "summary": args.summary, "checks": checks, "next": args.next,
             "order": int(run_id) if run_id else int(time.time() * 1000),
             "run_url": f"https://github.com/{request['repo']}/actions/runs/{run_id}" if run_id else ""}
    comment = github.upsert_status(request["repo"], request["thread"], state, args.author)
    if github.metadata(comment["body"]).get("order", -1) > state["order"]:
        print("A newer run owns publication.")
        return 0
    print(comment.get("html_url", comment["id"]))
    label = "trigger:" + github.metadata(comment["body"]).get("trigger", request["trigger"])
    github.api(f"repos/{request['repo']}/issues/{request['thread']}/labels", {"labels": [label]})
    if args.project:
        github.project(request["repo"], request["thread"], status, policy["project_owner"], policy["project_number"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
