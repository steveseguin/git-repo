# Copyright (C) 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for repo download's branch checkout behavior."""

import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import git_config
import project
from subcmds.download import Download
from subcmds.download import DownloadCommandError


class DownloadBranchTests(unittest.TestCase):
    """Exercise branch selection with real temporary Git repositories."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="repo-download-test-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.root),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "Repo test author",
                "GIT_COMMITTER_NAME": "Repo test committer",
                "GIT_AUTHOR_EMAIL": "repo@example.invalid",
                "GIT_COMMITTER_EMAIL": "repo@example.invalid",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.git("init", "-q", "--initial-branch=main")
        self.file = self.worktree / "file"
        self.file.write_text("base\n")
        self.git("add", "file")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "patches")
        self.file.write_text("first\n")
        self.git("commit", "-q", "-am", "first")
        self.first = self.git("rev-parse", "HEAD")
        self.file.write_text("second\n")
        self.git("commit", "-q", "-am", "second")
        self.second = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        gitdir = str(self.worktree / ".git")
        self.project = project.Project(
            manifest=SimpleNamespace(
                globalConfig=git_config.GitConfig(str(self.root / "config")),
                is_multimanifest=False,
            ),
            name="test-project",
            remote=project.RemoteSpec("origin"),
            gitdir=gitdir,
            objdir=gitdir,
            worktree=str(self.worktree),
            relpath="worktree",
            revisionExpr="main",
            revisionId=self.base,
        )
        self.cmd = Download()
        self.cmd.NAME = "download"
        self.opt, _ = self.cmd.OptionParser.parse_args(["-b", "review"])

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.worktree, text=True
        ).strip()

    def execute(self, commits):
        """Stub remote discovery/download, keeping local operations real."""
        ids = [(self.project, 123, i + 1) for i in range(len(commits))]
        changes = [
            SimpleNamespace(commit=commit, commits=[commit])
            for commit in commits
        ]
        with mock.patch.object(self.cmd, "_ParseChangeIds", return_value=ids):
            with mock.patch.object(
                self.project, "DownloadPatchSet", side_effect=changes
            ):
                self.cmd.Execute(self.opt, ["test-project", "123/1"])

    def test_new_branch_checks_out_change(self):
        self.execute([self.first])
        self.assertEqual("review", self.git("branch", "--show-current"))
        self.assertEqual(self.first, self.git("rev-parse", "HEAD"))
        self.assertEqual("first\n", self.file.read_text())

    def test_existing_current_branch_is_rejected(self):
        self.git("checkout", "-q", "-b", "review", self.first)
        with self.assertRaises(DownloadCommandError) as ctx:
            self.execute([self.second])
        self.assertIn(
            "branch review already exists",
            str(ctx.exception.aggregate_errors[0]),
        )
        self.assertEqual("review", self.git("branch", "--show-current"))
        self.assertEqual(self.first, self.git("rev-parse", "HEAD"))
        self.assertEqual("first\n", self.file.read_text())

    def test_existing_other_branch_is_rejected_without_checkout(self):
        self.git("branch", "review", self.first)
        with self.assertRaises(DownloadCommandError) as ctx:
            self.execute([self.second])
        self.assertIn(
            "branch review already exists",
            str(ctx.exception.aggregate_errors[0]),
        )
        self.assertEqual("main", self.git("branch", "--show-current"))
        self.assertEqual(self.base, self.git("rev-parse", "HEAD"))
        self.assertEqual(self.first, self.git("rev-parse", "review"))
        self.assertEqual("base\n", self.file.read_text())

    def test_second_change_for_same_project_is_not_silently_ignored(self):
        with self.assertRaises(DownloadCommandError) as ctx:
            self.execute([self.first, self.second])
        self.assertIn(
            "branch review already exists",
            str(ctx.exception.aggregate_errors[0]),
        )
        self.assertEqual("review", self.git("branch", "--show-current"))
        self.assertEqual(self.first, self.git("rev-parse", "HEAD"))

    def test_existing_branch_can_still_cherry_pick(self):
        self.git("branch", "review", self.base)
        self.opt.cherrypick = True
        self.execute([self.first])
        self.assertEqual("review", self.git("branch", "--show-current"))
        self.assertEqual("first\n", self.file.read_text())

    def test_existing_branch_can_still_fast_forward(self):
        self.git("branch", "review", self.base)
        self.opt.ffonly = True
        self.execute([self.first])
        self.assertEqual("review", self.git("branch", "--show-current"))
        self.assertEqual(self.first, self.git("rev-parse", "HEAD"))

    def test_existing_branch_can_still_revert(self):
        self.git("branch", "review", self.first)
        self.opt.revert = True
        self.execute([self.first])
        self.assertEqual("review", self.git("branch", "--show-current"))
        self.assertEqual("base\n", self.file.read_text())

    def test_without_branch_still_checks_out_change(self):
        self.opt.branch = None
        self.execute([self.second])
        self.assertEqual("", self.git("branch", "--show-current"))
        self.assertEqual(self.second, self.git("rev-parse", "HEAD"))
