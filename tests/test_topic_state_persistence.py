import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "persist_topic_state.sh"


class TopicStatePersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.remote = directory / "origin.git"
        self.runner = directory / "runner"
        self.editor = directory / "editor"
        self.environment = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
        }
        self.git(directory, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(directory, "clone", str(self.remote), str(self.runner))
        self.commit_file(self.runner, "topics.json", '[{"status":"unused"}]\n')
        self.git(self.runner, "push", "origin", "HEAD:main")
        self.git(directory, "clone", str(self.remote), str(self.editor))

    def git(self, directory, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            cwd=directory, env=self.environment, check=True, capture_output=True, text=True,
        ).stdout

    def commit_file(self, directory, filename, content):
        (directory / filename).write_text(content)
        self.git(directory, "add", filename)
        self.git(directory, "commit", "-m", f"Update {filename}")

    def persist(self):
        return subprocess.run(
            ["bash", str(SCRIPT), "Update topic state"], cwd=self.runner,
            env=self.environment, capture_output=True, text=True,
        )

    def test_rebases_unrelated_changes_and_persists_reservation_and_completion(self):
        self.commit_file(self.editor, "readme.txt", "Concurrent editorial update")
        self.git(self.editor, "push", "origin", "HEAD:main")
        (self.runner / "topics.json").write_text('[{"status":"delivery_pending"}]\n')
        reservation = self.persist()
        self.assertEqual(reservation.returncode, 0, reservation.stderr)
        self.assertIn("delivery_pending", self.git(self.remote, "show", "main:topics.json"))
        self.assertIn("Concurrent editorial update", self.git(self.remote, "show", "main:readme.txt"))

        (self.runner / "topics.json").write_text('[{"status":"used"}]\n')
        final = self.persist()
        self.assertEqual(final.returncode, 0, final.stderr)
        self.assertIn('"used"', self.git(self.remote, "show", "main:topics.json"))

    def test_conflicting_topic_update_does_not_overwrite_main(self):
        self.commit_file(self.editor, "topics.json", '[{"status":"needs_legal_review"}]\n')
        self.git(self.editor, "push", "origin", "HEAD:main")
        (self.runner / "topics.json").write_text('[{"status":"delivery_pending"}]\n')

        result = self.persist()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Topic state conflicts", result.stderr)
        self.assertIn("needs_legal_review", self.git(self.remote, "show", "main:topics.json"))
        self.assertEqual(self.git(self.runner, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
