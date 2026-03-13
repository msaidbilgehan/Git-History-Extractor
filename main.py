#!/usr/bin/env python3
"""Git Commit History Extractor.

A production-grade CLI tool for extracting git commit history with
configurable detail levels, multiple export formats, time range filtering,
and rich analysis features.

Usage:
    python git-commit-history-extractor.py /path/to/repo [OPTIONS]

Examples:
    # JSON output with default detail
    python git-commit-history-extractor.py /path/to/repo

    # Markdown report with full detail
    python git-commit-history-extractor.py /path/to/repo -f markdown -d full

    # CSV with date range and author filter
    python git-commit-history-extractor.py /path/to/repo -f csv --from 2025-01-01 --to 2025-06-01 --author "John"

    # Custom template format
    python git-commit-history-extractor.py /path/to/repo -f custom --template "{hash_short} | {author_name} | {subject}"
"""

from __future__ import annotations

__version__ = "1.0.0"

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
import textwrap
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COMMIT_SEP = "---GHE_COMMIT_a8f3e---"
_FIELD_SEP = "---GHE_FIELD_a8f3e---"

_GIT_LOG_FORMAT = (
    f"{_COMMIT_SEP}"
    f"{_FIELD_SEP}%H"
    f"{_FIELD_SEP}%h"
    f"{_FIELD_SEP}%an"
    f"{_FIELD_SEP}%ae"
    f"{_FIELD_SEP}%aI"
    f"{_FIELD_SEP}%cn"
    f"{_FIELD_SEP}%cI"
    f"{_FIELD_SEP}%s"
    f"{_FIELD_SEP}%b"
    f"{_FIELD_SEP}"
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ExtractorError(Exception):
    """Base exception for the extractor."""


class GitNotFoundError(ExtractorError):
    """Raised when git binary is not found on PATH."""


class InvalidRepoError(ExtractorError):
    """Raised when the provided path is not a valid git repository."""


class GitCommandError(ExtractorError):
    """Raised when a git command fails."""

    def __init__(self, command: str, stderr: str, returncode: int) -> None:
        self.command = command
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"git command failed (exit {returncode}): {stderr.strip()}")


class TemplateError(ExtractorError):
    """Raised when a custom template string is invalid."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DetailLevel(Enum):
    """Controls the amount of detail included in export output."""

    LOW = "low"
    DEFAULT = "default"
    FULL = "full"


class ExportFormat(Enum):
    """Supported export formats."""

    JSON = "json"
    MARKDOWN = "markdown"
    CSV = "csv"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileChange:
    """Represents a single file change within a commit."""

    path: str
    additions: int
    deletions: int


@dataclass(frozen=True, slots=True)
class CommitData:
    """Represents a single git commit with all available metadata."""

    hash: str
    hash_short: str
    author_name: str
    author_email: str
    author_date: datetime
    committer_name: str
    committer_date: datetime
    subject: str
    body: str
    files_changed: tuple[FileChange, ...]
    insertions: int
    deletions: int

    @property
    def files_changed_count(self) -> int:
        """Return the number of files changed in this commit."""
        return len(self.files_changed)


@dataclass
class CommitFilter:
    """Encapsulates filtering parameters for git log queries."""

    date_from: str | None = None
    date_to: str | None = None
    branch: str | None = None
    author: str | None = None
    grep: str | None = None
    no_merges: bool = False

    def to_git_args(self) -> list[str]:
        """Convert filter fields to git CLI arguments."""
        args: list[str] = []
        if self.date_from:
            args.extend(["--after", self.date_from])
        if self.date_to:
            args.extend(["--before", self.date_to])
        if self.author:
            args.extend(["--author", self.author])
        if self.grep:
            args.extend(["--grep", self.grep])
        if self.no_merges:
            args.append("--no-merges")
        if self.branch:
            args.append(self.branch)
        return args


# ---------------------------------------------------------------------------
# Color Support
# ---------------------------------------------------------------------------


class Color:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    _enabled: bool = True

    @classmethod
    def disable(cls) -> None:
        """Disable all color output by clearing color strings."""
        cls._enabled = False
        cls.RESET = ""
        cls.BOLD = ""
        cls.DIM = ""
        cls.RED = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.BLUE = ""
        cls.MAGENTA = ""
        cls.CYAN = ""


# ---------------------------------------------------------------------------
# Progress Reporter
# ---------------------------------------------------------------------------


class ProgressReporter:
    """Simple stderr-based progress indicator."""

    def __init__(self, total: int, *, enabled: bool = True) -> None:
        self._total = total
        self._enabled = enabled and total > 0
        self._is_tty = sys.stderr.isatty()

    def update(self, current: int) -> None:
        """Update progress display."""
        if not self._enabled:
            return
        pct = (current / self._total) * 100 if self._total else 0
        msg = f"\rProcessing commit {current}/{self._total} ({pct:.1f}%)..."
        if self._is_tty:
            sys.stderr.write(msg)
            sys.stderr.flush()
        elif current == self._total or current % 100 == 0:
            sys.stderr.write(f"{msg}\n")

    def finish(self) -> None:
        """Clear the progress line."""
        if self._enabled and self._is_tty:
            sys.stderr.write("\r" + " " * 60 + "\r")
            sys.stderr.flush()


# ---------------------------------------------------------------------------
# Git Repository Interface
# ---------------------------------------------------------------------------


class GitRepo:
    """Handles all interactions with a git repository via subprocess."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    @property
    def path(self) -> Path:
        """Return the resolved repository path."""
        return self._path

    def validate(self) -> None:
        """Validate that the path is a valid git repository.

        Raises:
            GitNotFoundError: If git is not installed.
            InvalidRepoError: If the path is not a valid git repository.
        """
        if shutil.which("git") is None:
            raise GitNotFoundError(
                "git is not installed or not found on PATH. "
                "Please install git and try again."
            )

        git_dir = self._path / ".git"
        if not git_dir.exists():
            raise InvalidRepoError(
                f"'{self._path}' is not a valid git repository. "
                f"No .git directory found."
            )

        result = self._run_git(["rev-parse", "--is-inside-work-tree"])
        if result.strip() != "true":
            raise InvalidRepoError(
                f"'{self._path}' is not inside a git work tree."
            )

    def fetch_all(self) -> None:
        """Run git fetch --all to get latest remote data."""
        sys.stderr.write("Fetching from all remotes...\n")
        self._run_git(["fetch", "--all"])
        sys.stderr.write("Fetch complete.\n")

    def count_commits(self, filters: CommitFilter) -> int:
        """Count commits matching the given filters."""
        cmd = ["rev-list", "--count"]
        filter_args = filters.to_git_args()
        if filters.branch:
            cmd.extend(filter_args)
        else:
            cmd.append("HEAD")
            cmd.extend(filter_args)
        try:
            result = self._run_git(cmd)
            return int(result.strip())
        except (GitCommandError, ValueError):
            return 0

    def get_raw_log(self, filters: CommitFilter) -> str:
        """Get raw git log output with structured format and numstat."""
        cmd = [
            "log",
            f"--pretty=format:{_GIT_LOG_FORMAT}",
            "--numstat",
        ]
        cmd.extend(filters.to_git_args())
        return self._run_git(cmd)

    def get_current_branch(self) -> str:
        """Get the name of the current branch."""
        try:
            result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            return result.strip()
        except GitCommandError:
            return "unknown"

    def _run_git(self, args: list[str]) -> str:
        """Execute a git command and return stdout.

        Args:
            args: Git subcommand and arguments.

        Returns:
            The stdout output of the command.

        Raises:
            GitCommandError: If the command exits with a non-zero code.
        """
        cmd = ["git", "-C", str(self._path), *args]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise GitCommandError(
                command=" ".join(cmd),
                stderr=result.stderr,
                returncode=result.returncode,
            )
        return result.stdout


# ---------------------------------------------------------------------------
# Log Parser
# ---------------------------------------------------------------------------


class LogParser:
    """Parses raw git log output into CommitData instances."""

    @staticmethod
    def parse(raw_output: str) -> list[CommitData]:
        """Parse raw git log output into a list of CommitData.

        Args:
            raw_output: Raw output from git log with sentinel-delimited format.

        Returns:
            List of parsed CommitData instances.
        """
        if not raw_output.strip():
            return []

        commits: list[CommitData] = []
        chunks = raw_output.split(_COMMIT_SEP)

        for chunk in chunks:
            if not chunk.strip():
                continue

            parts = chunk.split(_FIELD_SEP)
            # Expected parts: ['', hash, hash_short, author_name, author_email,
            #                   author_date, committer_name, committer_date,
            #                   subject, body, remainder_with_numstat]
            if len(parts) < 10:
                continue

            hash_full = parts[1].strip()
            hash_short = parts[2].strip()
            author_name = parts[3].strip()
            author_email = parts[4].strip()
            author_date_str = parts[5].strip()
            committer_name = parts[6].strip()
            committer_date_str = parts[7].strip()
            subject = parts[8].strip()
            body = parts[9].strip()

            # Parse dates
            author_date = LogParser._parse_date(author_date_str)
            committer_date = LogParser._parse_date(committer_date_str)

            # Parse numstat from remainder (after the last field separator)
            numstat_section = parts[10] if len(parts) > 10 else ""
            file_changes = LogParser._parse_numstat(numstat_section)

            total_insertions = sum(fc.additions for fc in file_changes)
            total_deletions = sum(fc.deletions for fc in file_changes)

            commits.append(
                CommitData(
                    hash=hash_full,
                    hash_short=hash_short,
                    author_name=author_name,
                    author_email=author_email,
                    author_date=author_date,
                    committer_name=committer_name,
                    committer_date=committer_date,
                    subject=subject,
                    body=body,
                    files_changed=tuple(file_changes),
                    insertions=total_insertions,
                    deletions=total_deletions,
                )
            )

        return commits

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """Parse an ISO 8601 date string from git."""
        try:
            return datetime.fromisoformat(date_str)
        except ValueError:
            return datetime.now(tz=timezone.utc)

    @staticmethod
    def _parse_numstat(section: str) -> list[FileChange]:
        """Parse numstat lines into FileChange instances."""
        changes: list[FileChange] = []
        for line in section.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", maxsplit=2)
            if len(parts) != 3:
                continue

            additions_str, deletions_str, path = parts
            # Binary files show '-' for additions/deletions
            additions = int(additions_str) if additions_str != "-" else 0
            deletions = int(deletions_str) if deletions_str != "-" else 0
            # Handle renames: old_path => new_path
            if " => " in path:
                path = path.split(" => ")[-1].rstrip("}")
                # Handle {old => new} format within path
                if "{" in path:
                    path = path.replace("{", "").replace("}", "")

            changes.append(FileChange(path=path, additions=additions, deletions=deletions))

        return changes


# ---------------------------------------------------------------------------
# Detail Level Projection
# ---------------------------------------------------------------------------


def project_commit(commit: CommitData, detail: DetailLevel) -> dict[str, Any]:
    """Project a CommitData to a dict based on the requested detail level.

    Args:
        commit: The full commit data.
        detail: The detail level to project to.

    Returns:
        A dictionary with only the fields appropriate for the detail level.
    """
    if detail == DetailLevel.LOW:
        return {
            "hash_short": commit.hash_short,
            "author_name": commit.author_name,
            "date": commit.author_date.strftime("%Y-%m-%d"),
            "subject": commit.subject,
        }

    if detail == DetailLevel.DEFAULT:
        return {
            "hash": commit.hash,
            "author_name": commit.author_name,
            "author_email": commit.author_email,
            "date": commit.author_date.isoformat(),
            "subject": commit.subject,
            "files_changed_count": commit.files_changed_count,
        }

    # FULL
    return {
        "hash": commit.hash,
        "hash_short": commit.hash_short,
        "author_name": commit.author_name,
        "author_email": commit.author_email,
        "author_date": commit.author_date.isoformat(),
        "committer_name": commit.committer_name,
        "committer_date": commit.committer_date.isoformat(),
        "subject": commit.subject,
        "body": commit.body,
        "insertions": commit.insertions,
        "deletions": commit.deletions,
        "files_changed": [
            {"path": fc.path, "additions": fc.additions, "deletions": fc.deletions}
            for fc in commit.files_changed
        ],
    }


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------


class Exporter(Protocol):
    """Protocol for export format implementations."""

    def export(self, commits: list[CommitData], detail: DetailLevel) -> str:
        """Export commits to a formatted string."""
        ...


class JSONExporter:
    """Exports commits as formatted JSON."""

    def export(self, commits: list[CommitData], detail: DetailLevel) -> str:
        projected = [project_commit(c, detail) for c in commits]
        return json.dumps(projected, indent=2, ensure_ascii=False, default=str)


class CSVExporter:
    """Exports commits as CSV."""

    def export(self, commits: list[CommitData], detail: DetailLevel) -> str:
        if not commits:
            return ""

        projected = [project_commit(c, detail) for c in commits]
        output = io.StringIO()
        fieldnames = list(projected[0].keys())

        # For CSV, convert complex fields to strings
        flat_rows: list[dict[str, str]] = []
        for row in projected:
            flat: dict[str, str] = {}
            for key, value in row.items():
                if isinstance(value, list):
                    flat[key] = "; ".join(
                        f"{item['path']} (+{item['additions']}/-{item['deletions']})"
                        if isinstance(item, dict)
                        else str(item)
                        for item in value
                    )
                else:
                    flat[key] = str(value)
            flat_rows.append(flat)

        writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(flat_rows)
        return output.getvalue()


class MarkdownExporter:
    """Exports commits as a readable Markdown report."""

    def export(self, commits: list[CommitData], detail: DetailLevel) -> str:
        if not commits:
            return "# Git Commit History Report\n\n_No commits found._\n"

        lines: list[str] = []
        lines.append("# Git Commit History Report\n")
        lines.append(f"**Total commits**: {len(commits)}  ")

        if commits:
            date_from = min(c.author_date for c in commits).strftime("%Y-%m-%d")
            date_to = max(c.author_date for c in commits).strftime("%Y-%m-%d")
            lines.append(f"**Date range**: {date_from} to {date_to}  ")

        lines.append("\n---\n")

        # Group commits by date
        grouped: dict[str, list[CommitData]] = {}
        for commit in commits:
            date_key = commit.author_date.strftime("%Y-%m-%d")
            grouped.setdefault(date_key, []).append(commit)

        for date_key in sorted(grouped.keys(), reverse=True):
            lines.append(f"## {date_key}\n")
            for commit in grouped[date_key]:
                self._render_commit(lines, commit, detail)
            lines.append("---\n")

        return "\n".join(lines)

    def _render_commit(
        self, lines: list[str], commit: CommitData, detail: DetailLevel
    ) -> None:
        """Render a single commit in markdown format."""
        if detail == DetailLevel.LOW:
            lines.append(
                f"- `{commit.hash_short}` {commit.subject} "
                f"— _{commit.author_name}_"
            )
            return

        lines.append(f"### `{commit.hash_short}` — {commit.subject}\n")
        lines.append(f"- **Author**: {commit.author_name} <{commit.author_email}>")
        lines.append(f"- **Date**: {commit.author_date.isoformat()}")

        if detail == DetailLevel.DEFAULT:
            lines.append(f"- **Files changed**: {commit.files_changed_count}")
            lines.append("")
            return

        # FULL detail
        lines.append(
            f"- **Stats**: {commit.files_changed_count} file(s), "
            f"+{commit.insertions}/-{commit.deletions}"
        )

        if commit.body:
            lines.append(f"\n> {commit.body.replace(chr(10), chr(10) + '> ')}\n")

        if commit.files_changed:
            lines.append("<details><summary>Changed files</summary>\n")
            lines.append("| File | Additions | Deletions |")
            lines.append("|------|-----------|-----------|")
            for fc in commit.files_changed:
                lines.append(f"| {fc.path} | +{fc.additions} | -{fc.deletions} |")
            lines.append("\n</details>\n")
        lines.append("")


class TemplateExporter:
    """Exports commits using a user-defined template string."""

    def __init__(self, template: str) -> None:
        self._template = template
        self._validate_template()

    def _validate_template(self) -> None:
        """Validate that the template uses known field names."""
        test_fields = {
            "hash": "", "hash_short": "", "author_name": "", "author_email": "",
            "date": "", "author_date": "", "committer_name": "", "committer_date": "",
            "subject": "", "body": "", "insertions": 0, "deletions": 0,
            "files_changed_count": 0, "files_changed": "",
        }
        try:
            self._template.format_map(test_fields)
        except KeyError as exc:
            valid_keys = ", ".join(sorted(test_fields.keys()))
            raise TemplateError(
                f"Unknown template field {exc}. Valid fields: {valid_keys}"
            ) from exc

    def export(self, commits: list[CommitData], detail: DetailLevel) -> str:
        lines: list[str] = []
        for commit in commits:
            data = project_commit(commit, detail)
            # Add extra fields that might be useful in templates
            data.setdefault("hash_short", commit.hash_short)
            data.setdefault("hash", commit.hash)
            data.setdefault("files_changed_count", commit.files_changed_count)
            data.setdefault("insertions", commit.insertions)
            data.setdefault("deletions", commit.deletions)
            data.setdefault("date", commit.author_date.strftime("%Y-%m-%d"))
            data.setdefault("author_date", commit.author_date.isoformat())
            data.setdefault("committer_name", commit.committer_name)
            data.setdefault("committer_date", commit.committer_date.isoformat())
            data.setdefault("body", commit.body)
            data.setdefault("author_name", commit.author_name)
            data.setdefault("author_email", commit.author_email)
            data.setdefault("subject", commit.subject)
            try:
                lines.append(self._template.format_map(data))
            except (KeyError, IndexError, ValueError) as exc:
                raise TemplateError(f"Template rendering failed: {exc}") from exc
        return "\n".join(lines)


def get_exporter(fmt: ExportFormat, template: str | None = None) -> Exporter:
    """Factory function to create the appropriate exporter.

    Args:
        fmt: The export format.
        template: Custom template string (required for CUSTOM format).

    Returns:
        An exporter instance.

    Raises:
        TemplateError: If CUSTOM format is selected without a template.
    """
    if fmt == ExportFormat.JSON:
        return JSONExporter()
    if fmt == ExportFormat.MARKDOWN:
        return MarkdownExporter()
    if fmt == ExportFormat.CSV:
        return CSVExporter()
    if fmt == ExportFormat.CUSTOM:
        if not template:
            raise TemplateError(
                "A --template string is required when using --format=custom. "
                "Example: --template \"{hash_short} | {author_name} | {subject}\""
            )
        return TemplateExporter(template)
    raise ExtractorError(f"Unsupported format: {fmt}")


# ---------------------------------------------------------------------------
# Statistics Summary
# ---------------------------------------------------------------------------


@dataclass
class StatsSummary:
    """Aggregated statistics about extracted commits."""

    total_commits: int
    authors: dict[str, int]
    date_range: tuple[str, str]
    total_insertions: int
    total_deletions: int
    most_active_day: str
    avg_commits_per_day: float
    top_files: list[tuple[str, int]]

    @classmethod
    def from_commits(cls, commits: list[CommitData]) -> StatsSummary:
        """Compute statistics from a list of commits."""
        if not commits:
            return cls(
                total_commits=0,
                authors={},
                date_range=("N/A", "N/A"),
                total_insertions=0,
                total_deletions=0,
                most_active_day="N/A",
                avg_commits_per_day=0.0,
                top_files=[],
            )

        author_counter: Counter[str] = Counter()
        day_counter: Counter[str] = Counter()
        file_counter: Counter[str] = Counter()
        total_ins = 0
        total_del = 0

        for commit in commits:
            author_counter[commit.author_name] += 1
            day_counter[commit.author_date.strftime("%A")] += 1
            total_ins += commit.insertions
            total_del += commit.deletions
            for fc in commit.files_changed:
                file_counter[fc.path] += 1

        dates = [c.author_date for c in commits]
        date_min = min(dates).strftime("%Y-%m-%d")
        date_max = max(dates).strftime("%Y-%m-%d")

        # Calculate average commits per day
        day_span = (max(dates) - min(dates)).days or 1
        avg_per_day = len(commits) / day_span

        most_active = day_counter.most_common(1)[0][0] if day_counter else "N/A"

        return cls(
            total_commits=len(commits),
            authors=dict(author_counter.most_common()),
            date_range=(date_min, date_max),
            total_insertions=total_ins,
            total_deletions=total_del,
            most_active_day=most_active,
            avg_commits_per_day=round(avg_per_day, 2),
            top_files=file_counter.most_common(10),
        )

    def render(self) -> str:
        """Render statistics as a formatted string."""
        c = Color
        lines: list[str] = [
            "",
            f"{c.BOLD}{c.CYAN}{'=' * 50}{c.RESET}",
            f"{c.BOLD}{c.CYAN}  Summary Statistics{c.RESET}",
            f"{c.BOLD}{c.CYAN}{'=' * 50}{c.RESET}",
            "",
            f"  {c.BOLD}Total commits:{c.RESET}     {self.total_commits}",
            f"  {c.BOLD}Date range:{c.RESET}        {self.date_range[0]} to {self.date_range[1]}",
            f"  {c.BOLD}Total insertions:{c.RESET}  {c.GREEN}+{self.total_insertions}{c.RESET}",
            f"  {c.BOLD}Total deletions:{c.RESET}   {c.RED}-{self.total_deletions}{c.RESET}",
            f"  {c.BOLD}Most active day:{c.RESET}   {self.most_active_day}",
            f"  {c.BOLD}Avg commits/day:{c.RESET}   {self.avg_commits_per_day}",
            "",
            f"  {c.BOLD}{c.YELLOW}Top Authors:{c.RESET}",
        ]

        for author, count in list(self.authors.items())[:10]:
            bar = "\u2588" * min(count, 40)
            lines.append(f"    {author:<30} {count:>4}  {c.BLUE}{bar}{c.RESET}")

        if self.top_files:
            lines.append("")
            lines.append(f"  {c.BOLD}{c.YELLOW}Most Changed Files:{c.RESET}")
            for filepath, count in self.top_files:
                display_path = filepath if len(filepath) <= 50 else f"...{filepath[-47:]}"
                lines.append(f"    {display_path:<50} {count:>4} changes")

        lines.append("")
        lines.append(f"{c.BOLD}{c.CYAN}{'=' * 50}{c.RESET}")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Argument Parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="git-commit-history-extractor",
        description="Extract git commit history with configurable detail and format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s /path/to/repo
              %(prog)s /path/to/repo -f markdown -d full -o report.md
              %(prog)s /path/to/repo -f csv --from 2025-01-01 --to 2025-06-01
              %(prog)s /path/to/repo -f custom --template "{hash_short} {subject}"
              %(prog)s /path/to/repo --author "John" --no-merges --stats
        """),
    )

    parser.add_argument(
        "repo_path",
        type=Path,
        help="Path to the git repository",
    )

    # Output control
    output_group = parser.add_argument_group("output options")
    output_group.add_argument(
        "-f", "--format",
        type=str,
        choices=[f.value for f in ExportFormat],
        default="json",
        dest="export_format",
        help="Export format (default: json)",
    )
    output_group.add_argument(
        "--template",
        type=str,
        default=None,
        help='Custom format template string, e.g. "{hash_short} | {subject}"',
    )
    output_group.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path (default: stdout)",
    )
    output_group.add_argument(
        "-d", "--detail",
        type=str,
        choices=[d.value for d in DetailLevel],
        default="default",
        help="Detail level (default: default)",
    )

    # Filtering
    filter_group = parser.add_argument_group("filtering options")
    filter_group.add_argument(
        "--from",
        type=str,
        default=None,
        dest="date_from",
        help='Start date (e.g. "2025-01-01" or "3 months ago")',
    )
    filter_group.add_argument(
        "--to",
        type=str,
        default=None,
        dest="date_to",
        help='End date (e.g. "2025-12-31" or "now")',
    )
    filter_group.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Branch name (default: current HEAD)",
    )
    filter_group.add_argument(
        "--author",
        type=str,
        default=None,
        help="Filter by author name (substring match)",
    )
    filter_group.add_argument(
        "--grep",
        type=str,
        default=None,
        help="Filter commits by message content (regex)",
    )
    filter_group.add_argument(
        "--no-merges",
        action="store_true",
        default=False,
        help="Exclude merge commits",
    )

    # Extras
    extras_group = parser.add_argument_group("extra options")
    extras_group.add_argument(
        "--stats",
        action="store_true",
        default=False,
        help="Print summary statistics after export",
    )
    extras_group.add_argument(
        "--fetch",
        action="store_true",
        default=False,
        help="Run 'git fetch --all' before extracting",
    )
    extras_group.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable colored terminal output",
    )
    extras_group.add_argument(
        "--progress",
        action="store_true",
        default=False,
        help="Show progress indicator",
    )
    extras_group.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose/debug output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the git commit history extractor.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Exit code (0=success, 1=known error, 2=unexpected error).
    """
    args = parse_args(argv)

    # Color setup
    if args.no_color or not sys.stderr.isatty():
        Color.disable()

    verbose: bool = args.verbose

    try:
        # Parse enum values
        detail = DetailLevel(args.detail)
        fmt = ExportFormat(args.export_format)

        # Validate repo
        repo = GitRepo(args.repo_path)
        repo.validate()

        if verbose:
            sys.stderr.write(f"Repository: {repo.path}\n")
            sys.stderr.write(f"Branch: {args.branch or repo.get_current_branch()}\n")

        # Optional fetch
        if args.fetch:
            repo.fetch_all()

        # Build filter
        commit_filter = CommitFilter(
            date_from=args.date_from,
            date_to=args.date_to,
            branch=args.branch,
            author=args.author,
            grep=args.grep,
            no_merges=args.no_merges,
        )

        if verbose:
            sys.stderr.write(f"Filter args: {commit_filter.to_git_args()}\n")

        # Get raw log
        raw_log = repo.get_raw_log(commit_filter)

        # Parse commits
        commits = LogParser.parse(raw_log)

        if verbose:
            sys.stderr.write(f"Parsed {len(commits)} commits.\n")

        if not commits:
            sys.stderr.write(
                f"{Color.YELLOW}Warning: No commits found matching the given filters.{Color.RESET}\n"
            )

        # Progress (retroactive reporting since parsing is already done,
        # but useful for export of large datasets)
        progress = ProgressReporter(len(commits), enabled=args.progress)
        for i in range(len(commits)):
            progress.update(i + 1)
        progress.finish()

        # Export
        exporter = get_exporter(fmt, args.template)
        output = exporter.export(commits, detail)

        # Write output
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            sys.stderr.write(
                f"{Color.GREEN}Output written to: {args.output}{Color.RESET}\n"
            )
        else:
            sys.stdout.write(output)
            if output and not output.endswith("\n"):
                sys.stdout.write("\n")

        # Statistics
        if args.stats:
            stats = StatsSummary.from_commits(commits)
            sys.stderr.write(stats.render())

        return 0

    except ExtractorError as exc:
        sys.stderr.write(f"{Color.RED}Error: {exc}{Color.RESET}\n")
        if verbose:
            traceback.print_exc(file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        return 130

    except Exception as exc:
        sys.stderr.write(f"{Color.RED}Unexpected error: {exc}{Color.RESET}\n")
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
