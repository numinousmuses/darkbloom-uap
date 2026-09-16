"""Normalize GitHub events and authorize spending before any agent runs."""

import hashlib
import json
import re

from .github import api


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def admit(event, event_name, repo, policy, *, call=api):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid repository")
    if event.get("repository", {}).get("full_name", repo) != repo:
        raise ValueError("Event repository mismatch")
    trigger, role, thread, instruction = "", "", 0, ""
    internal_review = False
    if event_name == "pull_request_target":
        if event.get("action") not in ("opened", "reopened", "synchronize", "ready_for_review"):
            return None
        thread = int(event["number"])
        trigger, role = "ci", "reviewer"
        instruction = "Review this pull request's changes. Report actionable findings. Do not edit files."
        identity = {"pr": thread}
    elif event_name == "issue_comment":
        if event.get("action") != "created" or event["sender"].get("type") == "Bot":
            return None
        body = event["comment"]["body"]
        match = re.fullmatch(r"/darkbloom (fix|review|retry)(?:[ \t]+([\s\S]{1,12000}))?\s*", body)
        if not match:
            return None
        actor = event["sender"]["login"]
        permission = call(f"repos/{repo}/collaborators/{actor}/permission")["permission"]
        if permission not in policy["allowed_permissions"]:
            raise PermissionError("Only repository collaborators can request agent work")
        thread = int(event["issue"]["number"])
        trigger = "instructed"
        role = "implementer" if match[1] == "fix" else "reviewer"
        issue = call(f"repos/{repo}/issues/{thread}")
        instruction = f"Task: {issue['title']}\n\n{issue.get('body') or ''}\n\nRequested action: {body}"
        identity = {"comment": event["comment"]["id"], "body": digest(body)}
    elif event_name in ("schedule", "workflow_dispatch"):
        inputs = event.get("inputs", {})
        if event_name == "workflow_dispatch":
            actor = event["sender"]["login"]
            internal_review = actor == "github-actions[bot]" and event["sender"].get("type") == "Bot" and inputs.get("mode") == "review"
            if not internal_review and call(f"repos/{repo}/collaborators/{actor}/permission")["permission"] not in policy["allowed_permissions"]:
                raise PermissionError("Dispatch requires repository write permission")
        thread = int(inputs.get("issue") or policy["schedule_issue"])
        if thread < 1:
            return None
        trigger = "schedule" if event_name == "schedule" or inputs.get("trigger") == "schedule" else "instructed"
        role = "reviewer" if trigger == "schedule" or inputs.get("mode") == "review" else "implementer"
        issue = call(f"repos/{repo}/issues/{thread}")
        instruction = f"Task: {issue['title']}\n\n{issue.get('body') or ''}"
        identity = {"thread": thread, "schedule": event.get("schedule"), "occurrence": inputs.get("occurrence", "manual")}
    else:
        return None
    issue = call(f"repos/{repo}/issues/{thread}")
    origin = next((label["name"].split(":", 1)[1] for label in issue.get("labels", [])
                   if label["name"] in ("trigger:instructed", "trigger:schedule")), trigger)
    pr = call(f"repos/{repo}/pulls/{thread}") if issue.get("pull_request") else None
    if internal_review and (not pr or pr["head"].get("repo", {}).get("full_name") != repo
                            or not pr["head"].get("ref", "").startswith("automation/task-")):
        raise PermissionError("Internal dispatch is restricted to generated PR reviews")
    if pr:
        if pr["state"] != "open" or not pr.get("merge_commit_sha"):
            raise ValueError("PR must be open and have a merge revision")
        source, head, base = pr["merge_commit_sha"], pr["head"]["sha"], pr["base"]["sha"]
    else:
        branch = call(f"repos/{repo}")["default_branch"]
        source = call(f"repos/{repo}/commits/{branch}")["sha"]
        head, base = source, source
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (source, head, base)):
        raise ValueError("Source revisions must be immutable commit SHAs")
    request = {"schema": 1, "repo": repo, "thread": thread, "trigger": trigger, "role": role,
               "source": source, "head": head, "base": base, "is_pr": pr is not None,
               "instruction": instruction, "policy_digest": digest(policy)}
    request["origin"] = origin
    request["request_id"] = digest({**identity, "repo": repo, "trigger": trigger,
                                    "source": source, "policy": digest(policy)})[:24]
    return request


def current(request, *, call=api):
    if not request["is_pr"]:
        return True
    pr = call(f"repos/{request['repo']}/pulls/{request['thread']}")
    return (pr["state"] == "open" and pr["head"]["sha"] == request["head"]
            and pr["base"]["sha"] == request["base"] and pr.get("merge_commit_sha") == request["source"])
