"""Scheduled full-repo documentation drift audit.

Walks the docs tree using folder indexes, asks the LLM to assess each
doc's staleness, and reports findings grouped by severity.
"""

import json
import os

from config import get_client, get_max_context_chars, get_model_name, truncate_content
from doc_index import get_doc_folders, get_docs_in_folder, get_docs_root, load_manifest
from security_utils import run_command_safe, sanitize_output


def run_audit(max_files=20):
    """Audit documentation for drift against the current codebase.

    Returns a list of {file, severity, reason} dicts.
    """
    docs_root = get_docs_root().resolve()
    findings = []
    files_checked = 0
    files_skipped = 0

    folders = get_doc_folders(docs_root)
    manifest = load_manifest(docs_root)

    for folder in folders:
        docs = get_docs_in_folder(folder, docs_root)
        for doc in docs:
            if files_checked >= max_files:
                files_skipped += 1
                continue

            rel_path = str(doc.relative_to(docs_root))
            try:
                content = doc.read_text(encoding="utf-8")
            except Exception:
                continue

            folder_info = manifest.get("folders", {}).get(folder, {})
            index_summary = folder_info.get("index", "")

            severity, reason = _assess_doc(rel_path, content, index_summary)
            if severity != "fresh":
                findings.append({"file": rel_path, "severity": severity, "reason": reason})
            files_checked += 1

    if files_skipped > 0:
        print(
            f"Audit budget reached: checked {files_checked} files, "
            f"skipped {files_skipped}. Increase audit-budget to cover more."
        )

    return findings


def _assess_doc(file_path, content, index_summary):
    """Ask the LLM to assess a single doc's staleness.

    Returns (severity, reason) where severity is "fresh", "stale", or "very-stale".
    """
    prompt = f"""Assess whether this documentation file is likely up to date with the current codebase.

File: {file_path}

Folder context: {index_summary[:500] if index_summary else "No index available"}

Documentation content (first 2000 chars):
{content[:2000]}

Rate the doc as one of:
- FRESH: content appears current, no obvious staleness
- STALE: some sections may be outdated (e.g. refers to old defaults, missing recent features)
- VERY-STALE: significant portions are likely incorrect or missing

Respond with exactly one line: SEVERITY: brief reason
Example: STALE: refers to v1 API but v2 has been released"""

    budget = get_max_context_chars()
    prompt = truncate_content(prompt, budget, label=f"audit prompt for {file_path}")

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.choices[0].message.content or "").strip()

        for severity in ("VERY-STALE", "STALE", "FRESH"):
            if severity in text.upper():
                reason = text.split(":", 1)[1].strip() if ":" in text else text
                return severity.lower(), reason

        return "stale", text[:200]
    except Exception as e:
        print(f"Warning: Could not assess {file_path}: {sanitize_output(str(e))}")
        return "fresh", ""


def format_audit_report(findings):
    """Format findings into a Markdown report grouped by severity."""
    if not findings:
        return "No documentation drift detected."

    lines = ["# Documentation Drift Audit", ""]

    for severity in ("very-stale", "stale"):
        group = [f for f in findings if f["severity"] == severity]
        if not group:
            continue
        label = "Very Stale" if severity == "very-stale" else "Stale"
        lines.append(f"## {label} ({len(group)} file{'s' if len(group) != 1 else ''})")
        lines.append("")
        for f in group:
            lines.append(f"- **{f['file']}**: {f['reason']}")
        lines.append("")

    return "\n".join(lines)


def post_audit_issue(findings, repo=None):
    """Create or update a single GitHub Issue with audit findings."""
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        print("Warning: GH_TOKEN not set, cannot post audit issue")
        return

    if repo is None:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set, cannot post audit issue")
        return

    label = "docs-drift-audit"
    title = "Documentation Drift Audit Report"
    body = format_audit_report(findings)

    run_command_safe(
        [
            "gh",
            "label",
            "create",
            label,
            "--description",
            "Automated docs drift audit",
            "--color",
            "FBCA04",
            "--force",
        ],
        check=False,
        env={**os.environ, "GH_TOKEN": gh_token},
    )

    existing = run_command_safe(
        [
            "gh",
            "issue",
            "list",
            "--label",
            label,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "1",
        ],
        check=False,
        env={**os.environ, "GH_TOKEN": gh_token},
    )

    try:
        issues = json.loads(existing.stdout.strip()) if existing.returncode == 0 else []
    except (json.JSONDecodeError, ValueError):
        issues = []

    if issues:
        issue_num = issues[0]["number"]
        run_command_safe(
            ["gh", "issue", "edit", str(issue_num), "--body", body],
            check=False,
            env={**os.environ, "GH_TOKEN": gh_token},
        )
        print(f"Updated existing audit issue #{issue_num}")
    else:
        result = run_command_safe(
            ["gh", "issue", "create", "--title", title, "--body", body, "--label", label],
            check=False,
            env={**os.environ, "GH_TOKEN": gh_token},
        )
        if result.returncode == 0:
            print(f"Created audit issue: {result.stdout.strip()}")
