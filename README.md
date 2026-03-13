# Git Commit History Extractor

A production-grade CLI tool for extracting git commit history with configurable detail levels, multiple export formats, flexible filtering, and rich analysis features.

Zero external dependencies — runs on Python 3.10+ standard library only.

## Features

- **3 Detail Levels** — Low (one-liner), Default (standard), Full (diff stats, file changes, commit body)
- **4 Export Formats** — JSON, Markdown, CSV, and custom template strings
- **Flexible Filtering** — Date range, branch, author, commit message grep, merge exclusion
- **Summary Statistics** — Top authors, most changed files, activity patterns, insertion/deletion totals
- **Remote Fetch** — Optionally fetch from all remotes before extracting
- **Progress Indicator** — Real-time progress for large repositories
- **Colored Output** — ANSI-colored terminal statistics (auto-disabled for non-TTY)
- **File Output** — Write directly to a file with `-o`

## Prerequisites

- **Python 3.10+**
- **Git** installed and available on `PATH`

## Installation

No installation required. Clone the repository and run the script directly:

```bash
git clone https://github.com/msaidbilgehan/Git-History-Extractor.git
cd Git-History-Extractor
```

## Quick Start

```bash
# Extract history from any git repository as JSON
python main.py /path/to/your/repo

# Generate a Markdown report with full detail
python main.py /path/to/your/repo -f markdown -d full

# Export to CSV with date range filtering
python main.py /path/to/your/repo -f csv --from 2025-01-01 --to 2025-12-31
```

## Usage

```
main.py <repo_path> [OPTIONS]
```

### Output Options

| Option | Description | Default |
|--------|-------------|---------|
| `-f`, `--format` | Export format: `json`, `markdown`, `csv`, `custom` | `json` |
| `-d`, `--detail` | Detail level: `low`, `default`, `full` | `default` |
| `-o`, `--output` | Write output to file instead of stdout | stdout |
| `--template` | Custom format template (required with `--format=custom`) | — |

### Filtering Options

| Option | Description | Default |
|--------|-------------|---------|
| `--from` | Start date (`2025-01-01`, `3 months ago`) | All history |
| `--to` | End date (`2025-12-31`, `now`) | All history |
| `--branch` | Branch name | Current HEAD |
| `--author` | Filter by author name (substring match) | All authors |
| `--grep` | Filter commits by message content (regex) | All commits |
| `--no-merges` | Exclude merge commits | Include all |
| `--first-parent` | Follow only first parent of merges (avoids duplicates) | Off |

### Extra Options

| Option | Description |
|--------|-------------|
| `--stats` | Print summary statistics after export |
| `--fetch` | Run `git fetch --all` before extracting |
| `--progress` | Show progress indicator |
| `--no-color` | Disable colored terminal output |
| `-v`, `--verbose` | Enable verbose/debug output |
| `--version` | Show version number |

## Detail Levels

### Low (`-d low`)

Minimal one-line-per-commit output:

```json
[
  {
    "hash_short": "a1b2c3d",
    "author_name": "Jane Doe",
    "date": "2025-03-15",
    "subject": "Fix authentication bug"
  }
]
```

### Default (`-d default`)

Standard commit information with file change count:

```json
[
  {
    "hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    "author_name": "Jane Doe",
    "author_email": "jane@example.com",
    "date": "2025-03-15T14:22:00+02:00",
    "subject": "Fix authentication bug",
    "files_changed_count": 3
  }
]
```

### Full (`-d full`)

Complete commit data including diff stats, commit body, and per-file changes:

```json
[
  {
    "hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    "hash_short": "a1b2c3d",
    "author_name": "Jane Doe",
    "author_email": "jane@example.com",
    "author_date": "2025-03-15T14:22:00+02:00",
    "committer_name": "Jane Doe",
    "committer_date": "2025-03-15T14:22:00+02:00",
    "subject": "Fix authentication bug",
    "body": "Resolved token expiry issue causing 401 errors.",
    "insertions": 42,
    "deletions": 18,
    "files_changed": [
      { "path": "src/auth.py", "additions": 30, "deletions": 15 },
      { "path": "tests/test_auth.py", "additions": 10, "deletions": 2 },
      { "path": "docs/auth.md", "additions": 2, "deletions": 1 }
    ]
  }
]
```

## Export Formats

### JSON (default)

```bash
python main.py /path/to/repo -f json
```

Structured JSON array. Pipe to `jq` for further processing:

```bash
python main.py /path/to/repo | jq '.[0:5]'
```

### Markdown

```bash
python main.py /path/to/repo -f markdown -d full -o report.md
```

Generates a readable report grouped by date with collapsible file change tables (in full detail mode).

### CSV

```bash
python main.py /path/to/repo -f csv -o history.csv
```

Tabular format ready for spreadsheets. Complex fields (file lists) are serialized as semicolon-separated strings.

### Custom Template

```bash
python main.py /path/to/repo -f custom \
  --template "{hash_short} | {author_name:<20} | {date} | {subject}"
```

Uses Python's `str.format_map` syntax. Available template fields:

| Field | Description | Example |
|-------|-------------|---------|
| `{hash}` | Full commit SHA | `a1b2c3d4e5f6...` |
| `{hash_short}` | Abbreviated SHA | `a1b2c3d` |
| `{author_name}` | Author name | `Jane Doe` |
| `{author_email}` | Author email | `jane@example.com` |
| `{date}` | Date (YYYY-MM-DD) | `2025-03-15` |
| `{author_date}` | Full ISO 8601 date | `2025-03-15T14:22:00+02:00` |
| `{committer_name}` | Committer name | `Jane Doe` |
| `{committer_date}` | Committer ISO date | `2025-03-15T14:22:00+02:00` |
| `{subject}` | Commit subject line | `Fix authentication bug` |
| `{body}` | Commit body text | `Detailed description...` |
| `{insertions}` | Total lines added | `42` |
| `{deletions}` | Total lines removed | `18` |
| `{files_changed_count}` | Number of files changed | `3` |
| `{files_changed_paths}` | Comma-separated file paths | `src/auth.py, tests/test_auth.py` |
| `{files_changed}` | Raw file change list (full detail) | `[{"path": "...", "additions": 5, ...}]` |

## Examples

### Custom Template with branch

```bash
python main.py /path/to/repo --from 2025-01-01 --to now --branch develop --progress -v --stats --template "## {subject}\n\n- **Files Changed:** {files_changed}\n- **Date:** {date}\n\n" -f custom -o /path/to/repo/git_history_summary.md
```

### Filter by date range and author

```bash
python main.py /path/to/repo \
  --from "2025-01-01" --to "2025-06-30" \
  --author "Jane" \
  -d full
```

### Generate a report excluding merge commits with statistics

```bash
python main.py /path/to/repo \
  -f markdown -d full -o report.md \
  --no-merges --stats
```

### Search commits by message content

```bash
python main.py /path/to/repo \
  --grep "fix.*auth" -d low
```

### Extract from a specific branch after fetching

```bash
python main.py /path/to/repo \
  --fetch --branch origin/develop -f csv -o develop-history.csv
```

### Quick one-liner log

```bash
python main.py /path/to/repo \
  -f custom --template "{hash_short} {date} {author_name}: {subject}"
```

### Statistics summary

```bash
python main.py /path/to/repo --stats 2>&1 >/dev/null
```

The `--stats` flag outputs to stderr, so you can capture data and stats separately:

```bash
# Data to file, stats to terminal
python main.py /path/to/repo -o data.json --stats
```

Sample statistics output:

```
==================================================
  Summary Statistics
==================================================

  Total commits:     142
  Date range:        2025-01-15 to 2025-06-01
  Total insertions:  +8432
  Total deletions:   -3210
  Most active day:   Wednesday
  Avg commits/day:   1.04

  Top Authors:
    Jane Doe                            87  ███████████████████
    John Smith                          42  █████████
    Alice Johnson                       13  ██

  Most Changed Files:
    src/core/engine.py                                   34 changes
    tests/test_engine.py                                 28 changes
    src/api/routes.py                                    19 changes

==================================================
```

## Architecture

The script follows a clean three-layer pipeline:

```
CLI (argparse) -> GitRepo (subprocess) -> LogParser -> Exporter -> stdout/file
```

| Component | Responsibility |
|-----------|----------------|
| `GitRepo` | Validates repository, executes git commands via subprocess |
| `LogParser` | Parses structured git log output into `CommitData` dataclasses |
| `CommitFilter` | Translates filter parameters into git CLI arguments |
| `Exporter` | Strategy pattern — JSON, Markdown, CSV, or Template exporters |
| `StatsSummary` | Aggregates commit data into statistics |

## Error Handling

The script provides clean, actionable error messages:

| Scenario | Behavior |
|----------|----------|
| Invalid repo path | `Error: '/path' is not a valid git repository. No .git directory found.` |
| Git not installed | `Error: git is not installed or not found on PATH.` |
| No matching commits | Warning on stderr, empty output on stdout |
| Invalid template field | Lists all valid field names in error message |
| Keyboard interrupt | Prints `Aborted.` and exits with code 130 |

Exit codes: `0` = success, `1` = known error, `2` = unexpected error, `130` = interrupted.

## License

This project is open source. See [LICENSE](LICENSE) for details.
