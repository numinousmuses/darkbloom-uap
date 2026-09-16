# Execution and verification

The workflow calls `admission.admit`, `sandbox.execute`, `verification.verify`,
and `workflow.finish`. GitHub Actions stores attempts and artifacts. No daemon or
database is required. The official UAP CLI validates the selected bundle and
renders its instructions before execution.

Admission queries the requester's current repository permission for explicit
commands. Public PRs enter CI regardless of their author's membership. The
request records source, head and base commits, trigger, instruction, and policy
digest. The workflow pins integration code to the admission checkout revision.

The agent container has a writable source export, read-only baseline and module
cache, no network, no Docker socket, no GitHub credential, a read-only root, and
bounded memory, CPU, process count and lifetime. A per-run Unix socket exposes
only the allowed model endpoints. Authentication remains in the host process;
the container receives a dummy credential. The connection has model, body-size,
output-token and request-count limits. These limits are not a monetary budget.

The agent leaves files, not commits. The host computes a patch using separate
Git metadata. It rejects changes to automation, workflow, and repository policy
files. A second container starts from the original source and applies the patch.
It runs an accepted verification recipe with no model connection. For requested
changes, a deterministic path map selects the affected component suite. Existing
Go regression tests are then restored from the accepted base and rerun, so a
candidate cannot earn a pass by deleting or skipping them. A receipt records
the source revision, patch digest, command, exit code, and conclusion.
The test environment disables Datadog container-origin enrichment. The existing
metric assertions expect application tags without a runner-specific container ID.

Only the publication job has GitHub write permissions. It checks the receipt
and patch digest, creates a signed commit and draft PR, then explicitly requests
verification of the published revision. GitHub does not recursively trigger PR
workflows for PRs created by its workflow token. Agent review findings remain
advisory even when deterministic tests pass.
Only read-only verification updates the checked PR's commit status. A requested
fix produces a separate draft PR; its receipt cannot mark the original head green.

The fork requires the GitHub Actions verification status, an up-to-date branch,
one approval, and resolved review conversations before merging, including for
administrators. Updating the verification policy itself remains an operator
change requiring its integration tests and a reviewed administration action.

One serialized workflow owns comments. It finds the marker only on comments
authored by its configured identity and edits that comment. It refuses duplicate
owned comments and ignores older run updates. A lost create response triggers
lookup, never an immediate second create. If lookup remains inconclusive, the
failed run requires operator reconciliation before retrying publication. GitHub
does not offer transactional create-if-absent comments. A canceled whole workflow
can leave an in-progress comment until the next run; this is not yet an automatic
recovery service.
The comment retains links to the five previous runs in a collapsed history.
Failed checks retain the agent's advisory findings and list failing Go tests.

The Project is a projection, not a queue. Its update runs independently of the
comment update and needs a credential with organization Project access. A failed
Project update fails the reporting job without rerunning the agent.

The accepted recipes currently cover coordinator packages, Responses handlers,
protocol tests and release-resolution script tests. Provider, sidecar and UI
changes are blocked pending their required environments. Passing one recipe
does not establish full-repository or production correctness. Database-backed
coverage requires its own configured database lane. Production deployment and
release workflows are not part of this contribution integration.

For Brainbase, retain the UAP bundles, request identity, verification recipes,
and sole GitHub publisher. Replace the container launch with the documented
workspace execution API, and prove candidate export, cancellation, and retries
against the real workspace before enabling it. No automatic UAP-to-workspace
import or migration of running sessions is assumed.
