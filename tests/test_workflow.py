"""Exercise the state-persistence workflow against temporary local Git remotes."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY / ".github" / "workflows" / "run-bot.yml"
GIT = shutil.which("git")


def state_commit_script():
    """Read the actual Bash run block without adding a YAML dependency."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step = lines.index("      - name: Commit updated state file")
    for index in range(step + 1, len(lines)):
        line = lines[index]
        if line.startswith("      - name:"):
            break
        if line == "        run: |":
            body = []
            for command in lines[index + 1 :]:
                if command.strip() and not command.startswith("          "):
                    break
                body.append(command[10:] if command.strip() else "")
            return "\n".join(body) + "\n"
    raise AssertionError("The state-commit step has no multiline Bash run block")


@unittest.skipUnless(GIT, "Git is required for local workflow integration tests")
class StateCommitWorkflowTests(unittest.TestCase):
    branch = "manual-dispatch-test"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="divar-workflow-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin.git"
        self.runner = self.root / "runner"
        self.writer = self.root / "writer"
        self.environment = {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"}
        self.environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_EDITOR="true",
            GIT_SEQUENCE_EDITOR="true",
            STATE_BRANCH=self.branch,
            GITHUB_REF_TYPE="branch",
        )
        self.git(self.root, "init", "--bare", "--initial-branch=master", str(self.origin))
        seed = self.root / "seed"
        self.git(self.root, "init", "--initial-branch=master", str(seed))
        self.identity(seed)
        self.write_tokens(seed, ["seen"])
        (seed / "README.md").write_text("Initial fixture\n", encoding="utf-8")
        self.git(seed, "add", "tokens.json", "README.md")
        self.git(seed, "commit", "-m", "Initial fixture")
        self.git(seed, "remote", "add", "origin", str(self.origin))
        self.git(seed, "push", "origin", "master")
        self.master_commit = self.git(seed, "rev-parse", "HEAD").stdout.strip()
        self.git(seed, "checkout", "-b", self.branch)
        self.git(seed, "push", "origin", self.branch)
        for checkout in (self.runner, self.writer):
            self.git(
                self.root,
                "clone",
                "--branch",
                self.branch,
                str(self.origin),
                str(checkout),
            )
            self.identity(checkout)

    def git(self, checkout, *arguments, check=True):
        return subprocess.run(
            [GIT, *arguments],
            cwd=checkout,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            timeout=20,
        )

    def identity(self, checkout):
        self.git(checkout, "config", "user.name", "Workflow fixture")
        self.git(checkout, "config", "user.email", "fixture@example.invalid")

    def write_tokens(self, checkout, tokens):
        (checkout / "tokens.json").write_text(
            json.dumps(tokens) + "\n", encoding="utf-8"
        )

    def origin_head(self, branch=None):
        return self.git(
            self.origin, "rev-parse", f"refs/heads/{branch or self.branch}"
        ).stdout.strip()

    def run_step(self):
        return subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-e",
                "-o",
                "pipefail",
                "-c",
                state_commit_script(),
            ],
            cwd=self.runner,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def commit_remote_change(self, path, contents):
        (self.writer / path).write_text(contents, encoding="utf-8")
        self.git(self.writer, "add", "--", path)
        self.git(self.writer, "commit", "-m", "Concurrent remote change")
        self.git(self.writer, "push", "origin", self.branch)
        return self.origin_head()

    def install_push_races(self, race_count):
        """Advance the local remote immediately before each selected push."""
        bin_directory = self.root / "bin"
        bin_directory.mkdir()
        wrapper = bin_directory / "git"
        wrapper.write_text(
            """#!/bin/bash
set -e -o pipefail
printf '%s\\n' "$*" >> "$GIT_TEST_COMMAND_LOG"
if [ "$1" = "push" ]; then
  count=0
  if [ -f "$GIT_TEST_PUSH_COUNT" ]; then
    read -r count < "$GIT_TEST_PUSH_COUNT"
  fi
  count=$((count + 1))
  printf '%s\\n' "$count" > "$GIT_TEST_PUSH_COUNT"
  if [ "$count" -le "$GIT_TEST_RACE_LIMIT" ]; then
    file="race-$count.txt"
    printf 'Concurrent change %s\\n' "$count" > "$GIT_TEST_WRITER/$file"
    "$REAL_GIT" -C "$GIT_TEST_WRITER" add -- "$file"
    "$REAL_GIT" -C "$GIT_TEST_WRITER" commit -m "Concurrent push $count"
    "$REAL_GIT" -C "$GIT_TEST_WRITER" push origin "$STATE_BRANCH"
  fi
fi
exec "$REAL_GIT" "$@"
""",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        self.command_log = self.root / "git-commands.log"
        self.environment.update(
            PATH=str(bin_directory) + os.pathsep + self.environment.get("PATH", ""),
            REAL_GIT=GIT,
            GIT_TEST_COMMAND_LOG=str(self.command_log),
            GIT_TEST_PUSH_COUNT=str(self.root / "push-count"),
            GIT_TEST_WRITER=str(self.writer),
            GIT_TEST_RACE_LIMIT=str(race_count),
        )

    def test_manual_dispatch_pushes_selected_branch(self):
        self.write_tokens(self.runner, ["seen", "new"])

        result = self.run_step()

        self.assert_success(result)
        self.assertEqual(
            self.origin_head(), self.git(self.runner, "rev-parse", "HEAD").stdout.strip()
        )
        self.assertNotEqual(self.origin_head(), self.master_commit)
        self.assertEqual(self.origin_head("master"), self.master_commit)
        self.assertEqual(
            json.loads(self.git(self.origin, "show", f"{self.branch}:tokens.json").stdout),
            ["seen", "new"],
        )
        self.assertEqual(self.git(self.runner, "status", "--porcelain").stdout, "")

    def test_unchanged_state_does_not_commit_or_require_remote(self):
        initial = self.git(self.runner, "rev-parse", "HEAD").stdout.strip()
        shutil.rmtree(self.origin)

        result = self.run_step()

        self.assert_success(result)
        self.assertIn("No state changes to commit", result.stdout)
        self.assertEqual(self.git(self.runner, "rev-parse", "HEAD").stdout.strip(), initial)
        self.assertEqual(self.git(self.runner, "status", "--porcelain").stdout, "")

    def test_tag_dispatch_cannot_create_a_branch_or_discard_state(self):
        tag = "release-fixture"
        self.git(self.runner, "tag", tag)
        self.git(self.runner, "push", "origin", f"refs/tags/{tag}")
        self.git(self.runner, "checkout", "--detach", tag)
        initial = self.git(self.runner, "rev-parse", "HEAD").stdout.strip()
        self.environment.update(STATE_BRANCH=tag, GITHUB_REF_TYPE="tag")
        self.write_tokens(self.runner, ["seen", "local"])
        self.install_push_races(0)

        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::State can only be saved to a branch", result.stdout)
        self.assertEqual(self.git(self.runner, "rev-parse", "HEAD").stdout.strip(), initial)
        self.assertEqual(json.loads((self.runner / "tokens.json").read_text()), ["seen", "local"])
        self.assertNotEqual(
            self.git(self.origin, "show-ref", "--verify", f"refs/heads/{tag}", check=False).returncode,
            0,
        )
        commands = self.command_log.read_text().splitlines()
        self.assertFalse(any(command.startswith("push ") for command in commands))

    def test_unrelated_remote_commit_is_preserved_by_rebase(self):
        remote_commit = self.commit_remote_change("notes.txt", "Remote notes\n")
        self.write_tokens(self.runner, ["seen", "new"])

        result = self.run_step()

        self.assert_success(result)
        self.assertEqual((self.runner / "notes.txt").read_text(), "Remote notes\n")
        self.assertEqual(json.loads((self.runner / "tokens.json").read_text()), ["seen", "new"])
        self.assertEqual(
            self.git(self.runner, "merge-base", "--is-ancestor", remote_commit, "HEAD").returncode,
            0,
        )
        self.assertEqual(
            self.origin_head(), self.git(self.runner, "rev-parse", "HEAD").stdout.strip()
        )

    def test_conflicting_state_aborts_without_discarding_local_state(self):
        remote_commit = self.commit_remote_change(
            "tokens.json", json.dumps(["seen", "remote"]) + "\n"
        )
        self.write_tokens(self.runner, ["seen", "local"])
        self.install_push_races(0)

        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::State changes conflict", result.stdout)
        self.assertEqual(json.loads((self.runner / "tokens.json").read_text()), ["seen", "local"])
        self.assertEqual(self.origin_head(), remote_commit)
        self.assertFalse((self.runner / ".git" / "rebase-merge").exists())
        self.assertFalse((self.runner / ".git" / "rebase-apply").exists())
        commands = self.command_log.read_text().splitlines()
        self.assertIn("rebase --abort", commands)
        self.assertFalse(any(command.startswith("push ") for command in commands))
        self.assertEqual(self.git(self.runner, "status", "--porcelain").stdout, "")

    def test_push_race_is_retried_with_new_remote_commit(self):
        self.write_tokens(self.runner, ["seen", "new"])
        self.install_push_races(1)

        result = self.run_step()

        self.assert_success(result)
        self.assertIn("Push attempt 1 failed", result.stdout)
        self.assertEqual((self.runner / "race-1.txt").read_text(), "Concurrent change 1\n")
        self.assertEqual(json.loads((self.runner / "tokens.json").read_text()), ["seen", "new"])
        commands = self.command_log.read_text().splitlines()
        pushes = [command for command in commands if command.startswith("push ")]
        self.assertEqual(len(pushes), 2)
        self.assertTrue(all(command == f"push origin HEAD:refs/heads/{self.branch}" for command in pushes))
        self.assertEqual(
            self.origin_head(), self.git(self.runner, "rev-parse", "HEAD").stdout.strip()
        )

    def test_repeated_push_races_fail_after_three_attempts_and_keep_state(self):
        self.write_tokens(self.runner, ["seen", "local"])
        self.install_push_races(3)

        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::Unable to push saved state after 3 attempts", result.stdout)
        commands = self.command_log.read_text().splitlines()
        pushes = [command for command in commands if command.startswith("push ")]
        self.assertEqual(len(pushes), 3)
        self.assertTrue(all(command == f"push origin HEAD:refs/heads/{self.branch}" for command in pushes))
        self.assertEqual(json.loads((self.runner / "tokens.json").read_text()), ["seen", "local"])
        self.assertEqual(
            json.loads(self.git(self.runner, "show", "HEAD:tokens.json").stdout),
            ["seen", "local"],
        )
        self.assertEqual(
            json.loads(self.git(self.origin, "show", f"{self.branch}:tokens.json").stdout),
            ["seen"],
        )


if __name__ == "__main__":
    unittest.main()
