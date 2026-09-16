"""Run against the built sandbox image: a skipped regression must not earn a pass."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from automation.verification import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp) / "repo"
        repo.mkdir()

        def git(*arguments):
            return subprocess.check_output(["git", *arguments], cwd=repo)

        git("init", "-q")
        (repo / "go.mod").write_text("module test.invalid/verification\n\ngo 1.25.0\n")
        package = repo / "coordinator/protocol"
        package.mkdir(parents=True)
        (package / "value.go").write_text("package protocol\nfunc Value() int { return 1 }\n")
        test = package / "value_test.go"
        test.write_text('package protocol\nimport "testing"\nfunc TestValue(t *testing.T) { if Value()!=2 { t.Fatal("known defect") } }\n')
        git("add", "-A")
        git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "Baseline")
        source = git("rev-parse", "HEAD").decode().strip()
        test.write_text('package protocol\nimport "testing"\nfunc TestValue(t *testing.T) { t.Skip("hide the defect") }\n')
        patch = Path(temp) / "candidate.patch"
        patch.write_bytes(git("diff"))
        result = verify(repo, source, patch, "protocol", args.out.resolve())
        assert result["exit_code"] == 0, "The candidate should appear to pass"
        assert result["accepted_tests_exit_code"] != 0, "The accepted regression must detect the defect"
        assert result["conclusion"] == "failed", "Skipping a regression must not earn a pass"
        print(json.dumps(result))


if __name__ == "__main__":
    main()
