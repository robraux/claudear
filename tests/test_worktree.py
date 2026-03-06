"""Tests for phase-aware worktree branch naming."""

from __future__ import annotations

from claudear.git.worktree import WorktreeManager


def test_branch_name_with_repo_and_phase(tmp_path):
    """Branch name includes repo key and phase."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mgr = WorktreeManager(str(repo))

    name = mgr.get_branch_name("ENG-123", repo_key="api", phase="spec")
    assert name == "eng-123/api/spec"


def test_branch_name_different_phases(tmp_path):
    """Different phases produce different branch names for same issue+repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mgr = WorktreeManager(str(repo))

    spec = mgr.get_branch_name("ENG-123", repo_key="api", phase="spec")
    impl = mgr.get_branch_name("ENG-123", repo_key="api", phase="implement")

    assert spec != impl
    assert spec == "eng-123/api/spec"
    assert impl == "eng-123/api/implement"


def test_branch_name_without_phase(tmp_path):
    """Without repo_key/phase, falls back to claudear/ prefix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mgr = WorktreeManager(str(repo))

    name = mgr.get_branch_name("ENG-123")
    assert name == "claudear/eng-123"
