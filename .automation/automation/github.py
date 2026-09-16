"""The only GitHub writer. Source and agent output are never executable input."""

import json
import re
import subprocess

MARKER = "<!-- darkbloom-automation-status:v1 -->"
CREATING = "automation:comment-pending"
META = re.compile(r"\n<!-- darkbloom-state:(\{[^\n]*\}) -->\s*\Z")
STATES = {"queued": "Queued", "working": "Working", "verifying": "Verifying",
          "passed": "Needs review", "failed": "Blocked", "blocked": "Blocked",
          "done": "Done", "stale": "Queued"}


def api(path, data=None, method=None):
    command = ["gh", "api", path]
    if data is not None:
        command += ["--input", "-"]
    if method:
        command += ["--method", method]
    result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def pages(path):
    result = []
    for page in range(1, 101):
        part = api(f"{path}?per_page=100&page={page}")
        result.extend(part)
        if len(part) < 100:
            return result
    raise RuntimeError("Pagination limit reached; refusing an incomplete status lookup")


def metadata(body):
    match = META.search(body or "")
    return json.loads(match.group(1)) if match else {}


def render(state):
    words = {"queued": "Queued", "working": "Investigating", "verifying": "Checking the change",
             "passed": "Checks passed", "failed": "Checks need attention", "blocked": "Needs input",
             "done": "Completed", "stale": "A newer revision needs checking"}
    lines = [MARKER, f"### Darkbloom automation · {words[state['status']]}", "",
             state["summary"].replace("<!--", "&lt;!--"), "", f"**Trigger:** {state['trigger']} · **Revision:** `{state['source'][:12]}`"]
    if state.get("checks"):
        lines += ["", "**Verification**", *[f"- {check}" for check in state["checks"]]]
    if state.get("next"):
        lines += ["", "**Next:** " + state["next"]]
    if state.get("run_url"):
        lines += ["", f"[Run details]({state['run_url']})"]
    # Compact state is for reconciliation. All facts needed by a person are above it.
    saved = {k: state[k] for k in ("request_id", "order", "trigger", "source", "status")}
    lines += ["", "<!-- darkbloom-state:" + json.dumps(saved, separators=(",", ":")) + " -->"]
    return "\n".join(lines)


def upsert_status(repo, thread, state, author, *, call=api, list_comments=None):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or thread < 1:
        raise ValueError("Invalid thread")
    if state["status"] not in STATES or state["trigger"] not in ("ci", "instructed", "schedule"):
        raise ValueError("Invalid status")
    listing = list_comments or (lambda: pages(f"repos/{repo}/issues/{thread}/comments"))
    owned = [c for c in listing() if c["user"]["login"] == author and MARKER in (c.get("body") or "")]
    if len(owned) > 1:
        raise RuntimeError("Multiple owned status comments; manual reconciliation required")
    if owned:
        old = metadata(owned[0]["body"])
        if old.get("order", -1) > state["order"]:
            return owned[0]
        # CI verifies an agent-created PR without erasing why the work was created.
        if old.get("trigger") in ("instructed", "schedule") and state["trigger"] == "ci":
            state = {**state, "trigger": old["trigger"]}
        return call(f"repos/{repo}/issues/comments/{owned[0]['id']}", {"body": render(state)}, "PATCH")
    labels = call(f"repos/{repo}/issues/{thread}/labels", None, "GET")
    if any(label["name"] == CREATING for label in labels):
        raise RuntimeError("A previous comment creation needs reconciliation before retry")
    # Durable intent survives a workflow rerun or host crash. Never create again
    # merely because a timed-out GitHub listing has not exposed the first comment.
    call(f"repos/{repo}/issues/{thread}/labels", {"labels": [CREATING]}, "POST")
    try:
        comment = call(f"repos/{repo}/issues/{thread}/comments", {"body": render(state)}, "POST")
    except (subprocess.CalledProcessError, OSError):
        # Never retry a create whose result is uncertain. An operator can reconcile
        # the failed Actions run after GitHub recovers; edits remain retryable.
        owned = [c for c in listing() if c["user"]["login"] == author and MARKER in (c.get("body") or "")]
        if len(owned) == 1:
            comment = owned[0]
        else:
            raise RuntimeError("Comment creation outcome uncertain; do not retry creation automatically") from None
    call(f"repos/{repo}/issues/{thread}/labels/automation%3Acomment-pending", None, "DELETE")
    return comment


def project(repo, thread, status, owner="numinous-technology", number=1):
    query = """query($owner:String!,$number:Int!){organization(login:$owner){projectV2(number:$number){id fields(first:50){nodes{... on ProjectV2SingleSelectField{id name options{id name}}}}}}}"""
    result = api("graphql", {"query": query, "variables": {"owner": owner, "number": number}})
    if result.get("errors"):
        raise RuntimeError("Project query failed")
    board = result["data"]["organization"]["projectV2"]
    issue = api(f"repos/{repo}/issues/{thread}")
    mutation = """mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}"""
    result = api("graphql", {"query": mutation, "variables": {"project": board["id"], "content": issue["node_id"]}})
    if result.get("errors"):
        raise RuntimeError("Project item update failed")
    item = result["data"]["addProjectV2ItemById"]["item"]["id"]
    field = next(f for f in board["fields"]["nodes"] if f.get("name") == "Status")
    option = next(o["id"] for o in field["options"] if o["name"].lower() == STATES[status].lower())
    mutation = """mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{id}}}"""
    result = api("graphql", {"query": mutation, "variables": {"project": board["id"], "item": item,
                    "field": field["id"], "option": option}})
    if result.get("errors"):
        raise RuntimeError("Project status update failed")
    return item
