import json
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "app.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "init")
    return repo


def test_review_packet_puts_test_edits_first_and_needs_merge_after_evidence(tmp_path):
    from hermes_cli.review_packet import build_review_receipt

    repo = _make_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import greet\n\n\ndef test_greet():\n    assert greet() == 'hello world'\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("def greet():\n    return 'hello world'\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change greeting")

    receipt = build_review_receipt(
        repo,
        base_ref="HEAD~1",
        intent="Change greeting behavior.",
        tests_run=["pytest tests/cli/test_review_packet.py"],
        evidence=["targeted pytest passed"],
    )

    packet = receipt["packet"]
    assert packet["intent"] == "Change greeting behavior."
    assert [item["path"] for item in packet["files_changed"]] == [
        "tests/test_app.py",
        "app.py",
    ]
    assert [item["path"] for item in packet["test_edits"]] == ["tests/test_app.py"]
    assert packet["blast_radius"]["risk_tier"] == "medium"
    assert receipt["merge_confidence"]["recommendation"] == "needs merge"
    assert receipt["merge_confidence"]["checks"]["tests_present"] is True
    assert receipt["merge_confidence"]["checks"]["dirty_worktree"] is False


def test_review_packet_holds_dirty_worktree_and_marks_inspection_lines(tmp_path):
    from hermes_cli.review_packet import build_review_receipt

    repo = _make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    receipt = build_review_receipt(
        repo,
        intent="Try a dependency config change.",
        tests_run=[],
        evidence=[],
    )

    assert receipt["merge_confidence"]["recommendation"] == "hold"
    assert receipt["merge_confidence"]["checks"]["dirty_worktree"] is True
    assert "pyproject.toml" in receipt["merge_confidence"]["reasons"][0]
    assert receipt["packet"]["human_must_inspect"][0]["path"] == "pyproject.toml"
    assert receipt["packet"]["human_must_inspect"][0]["line"] == 1


def test_review_packet_no_diff_does_not_need_merge(tmp_path):
    from hermes_cli.review_packet import build_review_receipt

    repo = _make_repo(tmp_path)
    receipt = build_review_receipt(repo, base_ref="HEAD", tests_run=[])

    assert receipt["packet"]["files_changed"] == []
    assert receipt["merge_confidence"]["recommendation"] == "does not need merge"
    assert receipt["merge_confidence"]["checks"]["has_changes"] is False


def test_review_packet_does_not_flag_section_rules_as_conflict_markers(tmp_path):
    from hermes_cli.review_packet import build_review_receipt

    repo = _make_repo(tmp_path)
    (repo / "app.py").write_text(
        "# =============================\n"
        "def greet():\n"
        "    return 'hello world'\n",
        encoding="utf-8",
    )
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "separator comment")

    receipt = build_review_receipt(
        repo,
        base_ref="HEAD~1",
        tests_run=["pytest tests/cli/test_review_packet.py"],
    )

    assert receipt["merge_confidence"]["checks"]["conflict_markers"] is False
    assert receipt["merge_confidence"]["recommendation"] == "needs merge"


def test_review_packet_cli_emits_json_receipt(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "README.md").write_text("# Demo\n\nsmall doc edit\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "docs")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "review-packet",
            "--path",
            str(repo),
            "--base",
            "HEAD~1",
            "--intent",
            "Document the demo.",
            "--test",
            "not run: docs only",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["packet"]["intent"] == "Document the demo."
    assert payload["packet"]["files_changed"][0]["path"] == "README.md"
    assert payload["merge_confidence"]["recommendation"] == "needs merge"
