"""Git review packet and merge-confidence receipt helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable


_DEFAULT_TIMEOUT_SECONDS = 15
_HIGH_RISK_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Dockerfile",
}
_HIGH_RISK_PREFIXES = (
    ".github/",
    "gateway/",
    "plugins/memory/",
    "tools/environments/",
)
_HIGH_RISK_EXACT = {
    "run_agent.py",
    "model_tools.py",
    "toolsets.py",
    "cli.py",
    "hermes_cli/main.py",
}


class ReviewPacketError(RuntimeError):
    """Raised when the review packet cannot be built."""


_PROTECTED_BRANCHES = {"main", "master", "trunk"}


def _run_git(repo: Path, args: list[str], *, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_PAGER": "cat"},
        )
    except FileNotFoundError as exc:
        raise ReviewPacketError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReviewPacketError(f"git command timed out after {timeout}s") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ReviewPacketError(detail or f"git {' '.join(args)} failed")
    return completed.stdout or ""


def _repo_root(path: Path | str) -> Path:
    raw = Path(path).expanduser().resolve()
    start = raw if raw.is_dir() else raw.parent
    output = _run_git(start, ["rev-parse", "--show-toplevel"])
    return Path(output.strip()).resolve()


def _diff_args(base_ref: str | None) -> list[str]:
    base = (base_ref or "HEAD").strip() or "HEAD"
    return [base, "--"]


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith(".spec.js")
        or name.endswith(".spec.ts")
    )


def _is_doc_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in {".md", ".rst", ".txt", ".adoc"} or path.startswith("docs/")


def _path_risk(path: str) -> str:
    name = Path(path).name
    if (
        path in _HIGH_RISK_EXACT
        or name in _HIGH_RISK_NAMES
        or path.startswith(_HIGH_RISK_PREFIXES)
    ):
        return "high"
    if _is_doc_path(path):
        return "low"
    if path.startswith(("tests/", "skills/", "optional-skills/")):
        return "medium"
    if Path(path).suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx"}:
        return "medium"
    return "medium"


def _overall_risk(paths: Iterable[str]) -> str:
    tiers = [_path_risk(path) for path in paths]
    if "high" in tiers:
        return "high"
    if "medium" in tiers:
        return "medium"
    if "low" in tiers:
        return "low"
    return "none"


def _parse_name_status(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        result[path] = status
    return result


def _parse_numstat(output: str) -> dict[str, tuple[int | None, int | None]]:
    result: dict[str, tuple[int | None, int | None]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, path = parts[0], parts[1], parts[-1]
        added = None if added_raw == "-" else int(added_raw)
        deleted = None if deleted_raw == "-" else int(deleted_raw)
        result[path] = (added, deleted)
    return result


def _parse_untracked(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines()[1:]:
        if line.startswith("?? "):
            paths.append(line[3:].strip())
    return paths


def _hunk_lines(diff_output: str) -> dict[str, int]:
    lines: dict[str, int] = {}
    current_path = ""
    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        if not current_path or not line.startswith("@@ "):
            continue
        match = re.search(r"\+(\d+)", line)
        if match and current_path not in lines:
            lines[current_path] = int(match.group(1))
    return lines


def _branch_details(repo: Path) -> dict[str, object]:
    branch_name = ""
    upstream = ""
    ahead = 0
    behind = 0
    raw = _run_git(repo, ["status", "--porcelain=v2", "--branch"])
    for line in raw.splitlines():
        if line.startswith("# branch.head "):
            branch_name = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ").strip()
        elif line.startswith("# branch.ab "):
            match = re.search(r"\+(\d+)\s+-(\d+)", line)
            if match:
                ahead = int(match.group(1))
                behind = int(match.group(2))

    protected = branch_name in _PROTECTED_BRANCHES
    return {
        "branch": branch_name,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "protected": protected,
    }


def _status_summary(status_output: str, branch_details: dict[str, object]) -> tuple[bool, bool, str]:
    lines = status_output.splitlines()
    branch_line = lines[0] if lines else ""
    dirty = any(line and not line.startswith("## ") for line in lines)
    stale = bool(branch_details.get("behind", 0))
    return dirty, stale, branch_line


def _head_sha(repo: Path) -> str:
    return _run_git(repo, ["rev-parse", "--short=12", "HEAD"]).strip()


def _untracked_file_entry(repo: Path, path: str) -> dict:
    target = repo / path
    added = None
    if target.is_file():
        try:
            added = len(target.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            added = None
    return {
        "path": path,
        "status": "??",
        "added": added,
        "deleted": 0 if added is not None else None,
        "risk": _path_risk(path),
        "is_test": _is_test_path(path),
    }


def _file_entries(repo: Path, base_ref: str | None, status_output: str) -> list[dict]:
    name_status = _parse_name_status(
        _run_git(repo, ["diff", "--name-status", *_diff_args(base_ref)])
    )
    numstat = _parse_numstat(
        _run_git(repo, ["diff", "--numstat", *_diff_args(base_ref)])
    )

    entries: list[dict] = []
    for path, status in name_status.items():
        added, deleted = numstat.get(path, (None, None))
        entries.append(
            {
                "path": path,
                "status": status,
                "added": added,
                "deleted": deleted,
                "risk": _path_risk(path),
                "is_test": _is_test_path(path),
            }
        )

    known = {entry["path"] for entry in entries}
    for path in _parse_untracked(status_output):
        if path not in known:
            entries.append(_untracked_file_entry(repo, path))

    return sorted(entries, key=lambda item: (not item["is_test"], item["path"]))


def _risk_notes(
    files: list[dict],
    dirty: bool,
    stale: bool,
    branch_details: dict[str, object],
) -> list[str]:
    notes: list[str] = []
    high_risk = [item["path"] for item in files if item["risk"] == "high"]
    if high_risk:
        notes.append("High-risk paths changed: " + ", ".join(high_risk))
    if dirty:
        dirty_paths = [item["path"] for item in files]
        detail = ", ".join(dirty_paths) if dirty_paths else "local changes"
        notes.append(f"Dirty worktree: {detail}")
    branch_name = str(branch_details.get("branch") or "")
    if branch_details.get("protected"):
        notes.append(f"Protected branch checked out: {branch_name}.")
    upstream = str(branch_details.get("upstream") or "")
    ahead = int(branch_details.get("ahead", 0) or 0)
    behind = int(branch_details.get("behind", 0) or 0)
    if upstream:
        notes.append(
            f"Upstream drift vs {upstream}: ahead {ahead}, behind {behind}."
        )
    elif stale:
        notes.append("Branch is behind its upstream; refresh before merge.")
    code_without_tests = [
        item["path"]
        for item in files
        if item["risk"] in {"medium", "high"}
        and not item["is_test"]
        and not _is_doc_path(item["path"])
    ]
    if code_without_tests:
        notes.append("Code/config paths need test evidence: " + ", ".join(code_without_tests))
    return notes


def _human_inspect(files: list[dict], hunk_lines: dict[str, int]) -> list[dict]:
    inspect: list[dict] = []
    for item in files:
        path = item["path"]
        if item["risk"] != "high" and item["status"] not in {"D", "??"}:
            continue
        inspect.append(
            {
                "path": path,
                "line": hunk_lines.get(path, 1),
                "reason": (
                    "high-risk path"
                    if item["risk"] == "high"
                    else "untracked or deleted path"
                ),
            }
        )
    return inspect


def _has_conflict_markers(repo: Path, files: list[dict]) -> bool:
    for item in files:
        path = repo / item["path"]
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("<<<<<<< ")
                or stripped == "======="
                or stripped.startswith(">>>>>>> ")
            ):
                return True
    return False


def _recommendation(
    *,
    has_changes: bool,
    dirty: bool,
    stale: bool,
    tests_present: bool,
    risk_tier: str,
    conflict_markers: bool,
    ai_findings: list[str],
    files: list[dict],
    branch_details: dict[str, object],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not has_changes:
        return "does not need merge", ["No changed files were detected."]
    if dirty:
        dirty_paths = [item["path"] for item in files]
        reasons.append("Dirty worktree: " + (", ".join(dirty_paths) or "local changes"))
    if stale:
        upstream = str(branch_details.get("upstream") or "its upstream")
        ahead = int(branch_details.get("ahead", 0) or 0)
        behind = int(branch_details.get("behind", 0) or 0)
        reasons.append(f"Upstream drift vs {upstream}: ahead {ahead}, behind {behind}.")
    if conflict_markers:
        reasons.append("Conflict markers were found in changed files.")
    if ai_findings:
        reasons.append("AI review findings are present.")
    if risk_tier in {"medium", "high"} and not tests_present:
        reasons.append("No test or evidence input was provided for code/config changes.")

    if reasons:
        return "hold", reasons
    return "needs merge", ["Changed files have deterministic evidence and no hold signals."]


def build_review_receipt(
    path: Path | str = ".",
    *,
    base_ref: str | None = None,
    intent: str = "",
    tests_run: list[str] | None = None,
    evidence: list[str] | None = None,
    ai_findings: list[str] | None = None,
) -> dict:
    """Build a deterministic review packet and merge-confidence receipt."""

    repo = _repo_root(path)
    status_output = _run_git(repo, ["status", "--short", "--branch"])
    branch_details = _branch_details(repo)
    dirty, stale, branch_line = _status_summary(status_output, branch_details)
    files = _file_entries(repo, base_ref, status_output)
    paths = [item["path"] for item in files]
    diff_output = _run_git(repo, ["diff", "--unified=0", *_diff_args(base_ref)])
    hunk_lines = _hunk_lines(diff_output)
    tests = list(tests_run or [])
    evidence_items = list(evidence or [])
    findings = list(ai_findings or [])
    risk_tier = _overall_risk(paths)
    has_changes = bool(files)
    conflict_markers = _has_conflict_markers(repo, files)
    recommendation, reasons = _recommendation(
        has_changes=has_changes,
        dirty=dirty,
        stale=stale,
        tests_present=bool(tests or evidence_items),
        risk_tier=risk_tier,
        conflict_markers=conflict_markers,
        ai_findings=findings,
        files=files,
        branch_details=branch_details,
    )

    packet = {
        "intent": intent.strip() or "Not provided.",
        "blast_radius": {
            "risk_tier": risk_tier,
            "files_changed": len(files),
            "summary": f"{len(files)} changed file(s); highest risk: {risk_tier}.",
        },
        "files_changed": files,
        "tests_run": tests,
        "evidence": evidence_items,
        "test_edits": [item for item in files if item["is_test"]],
        "risk_notes": _risk_notes(files, dirty, stale, branch_details),
        "human_must_inspect": _human_inspect(files, hunk_lines),
    }

    return {
        "packet": packet,
        "merge_confidence": {
            "recommendation": recommendation,
            "reasons": reasons,
            "checks": {
                "repo_root": str(repo),
                "head_sha": _head_sha(repo),
                "base_ref": (base_ref or "HEAD").strip() or "HEAD",
                "has_changes": has_changes,
                "files_changed": len(files),
                "tests_present": bool(tests or evidence_items),
                "dirty_worktree": dirty,
                "stale_worktree": stale,
                "protected_branch": bool(branch_details.get("protected")),
                "branch_name": branch_details.get("branch") or "",
                "upstream": branch_details.get("upstream") or "",
                "ahead_count": int(branch_details.get("ahead", 0) or 0),
                "behind_count": int(branch_details.get("behind", 0) or 0),
                "conflict_markers": conflict_markers,
                "ai_findings_count": len(findings),
            },
            "worktree_state": {
                "status": branch_line,
                "dirty": dirty,
                "stale": stale,
                "branch": branch_details.get("branch") or "",
                "upstream": branch_details.get("upstream") or "",
                "ahead": int(branch_details.get("ahead", 0) or 0),
                "behind": int(branch_details.get("behind", 0) or 0),
                "protected": bool(branch_details.get("protected")),
            },
            "ai_review": {
                "status": "findings_provided" if findings else "placeholder_no_ai_findings",
                "findings": findings,
            },
        },
    }


def format_review_receipt(receipt: dict) -> str:
    """Format a phone-readable review packet."""

    packet = receipt["packet"]
    confidence = receipt["merge_confidence"]
    lines = [
        "Agent PR Review Packet",
        "",
        f"Intent: {packet['intent']}",
        f"Risk: {packet['blast_radius']['risk_tier']} ({packet['blast_radius']['files_changed']} file(s))",
        f"Recommendation: {confidence['recommendation']}",
        "",
        "Worktree state:",
        (
            f"- branch {confidence['worktree_state']['branch'] or '(detached)'}"
            + (" (protected)" if confidence["worktree_state"]["protected"] else "")
        ),
        (
            f"- upstream {confidence['worktree_state']['upstream']}: "
            f"ahead {confidence['worktree_state']['ahead']}, "
            f"behind {confidence['worktree_state']['behind']}"
            if confidence["worktree_state"]["upstream"]
            else "- upstream not configured"
        ),
        f"- status {confidence['worktree_state']['status']}",
        "",
        "Files changed:",
    ]
    if packet["files_changed"]:
        for item in packet["files_changed"]:
            marker = "test " if item["is_test"] else ""
            added = item["added"] if item["added"] is not None else "?"
            deleted = item["deleted"] if item["deleted"] is not None else "?"
            lines.append(
                f"- {marker}{item['path']} "
                f"({item['status']}, +{added}, -{deleted}, {item['risk']})"
            )
    else:
        lines.append("- none")

    lines.extend(["", "Tests and evidence:"])
    for item in packet["tests_run"] or ["not provided"]:
        lines.append(f"- {item}")
    for item in packet["evidence"]:
        lines.append(f"- evidence: {item}")

    lines.extend(["", "Risk notes:"])
    for item in packet["risk_notes"] or ["none"]:
        lines.append(f"- {item}")

    lines.extend(["", "Human must inspect:"])
    for item in packet["human_must_inspect"] or [
        {"path": "none", "line": "", "reason": ""}
    ]:
        if item["path"] == "none":
            lines.append("- none")
        else:
            lines.append(f"- {item['path']}:{item['line']} - {item['reason']}")

    lines.extend(["", "Merge-confidence receipt:"])
    for reason in confidence["reasons"]:
        lines.append(f"- {reason}")
    lines.append("Checks: " + json.dumps(confidence["checks"], sort_keys=True))
    return "\n".join(lines) + "\n"


def receipt_to_json(receipt: dict) -> str:
    return json.dumps(receipt, indent=2, sort_keys=True) + "\n"
