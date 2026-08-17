"""
Main orchestrator for code-to-docs GitHub Action.

Detects the command mode ([review-docs], [update-docs], [review-feature]),
coordinates file discovery, content generation, and PR/comment posting.

All business logic lives in dedicated modules:
- config.py: environment configuration
- github_ops.py: git and GitHub operations
- discovery.py: file discovery and AI selection
- generation.py: AI content generation and file I/O
- comments.py: PR comment building, parsing, and posting
- doc_index.py: semantic indexing system
- jira_integration.py: Jira/Confluence/Google Docs integration
- security_utils.py: credential sanitization and path validation
"""

import argparse
import difflib
import json
import os
import re
import subprocess
from pathlib import Path

from comments import (
    parse_previous_review,
    parse_update_instructions,
    post_review_comment,
)
from config import (
    get_client,
    get_max_context_chars,
    get_model_name,
    get_pr_title_prefix,
    load_style_config_from_branch,
)
from discovery import (
    ask_ai_for_relevant_files,
    find_relevant_files_optimized,
    get_file_content_or_summaries,
)
from doc_index import build_all_indexes, checkout_docs_from_base_branch
from generation import (
    ask_ai_for_updated_content,
    generate_updates_parallel,
    load_full_content,
    overwrite_file,
)
from github_ops import get_commit_info, get_diff, push_and_open_pr, setup_docs_environment
from jira_integration import (
    analyze_feature_coverage,
    fetch_jira_context_sync,
    format_feature_review_section,
    parse_feature_command,
)
from run_log import RunLog
from security_utils import run_command_safe, sanitize_output
from telemetry import UsageTracker

_GITHUB_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _get_pr_description(pr_number):
    """Fetch the PR title and body for context."""
    if not pr_number or pr_number == "unknown":
        return ""
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        return ""
    result = run_command_safe(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "title,body",
            "--jq",
            '.title + "\\n" + .body',
        ],
        check=False,
        env={**os.environ, "GH_TOKEN": gh_token},
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return ""


def _normalize_github_url(url):
    """Normalize a GitHub URL to https://github.com/owner/repo for comparison."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url[len("ssh://git@github.com/") :]
    elif url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:") :]
    return url


def _resolve_pr_push_target(pr_number):
    """Resolve the branch name, repo clone URL, and merge state for a PR.

    Returns (branch_name, clone_url, is_merged) or (None, None, False) on failure.
    For same-repo PRs, clone_url matches the current origin.
    For fork PRs, clone_url points to the fork.
    """
    if not pr_number or pr_number == "unknown":
        return None, None, False
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        return None, None, False
    result = run_command_safe(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "headRefName,headRepository,headRepositoryOwner,state",
            "--jq",
            "[.headRefName, .headRepositoryOwner.login, .headRepository.name, .state] | @tsv",
        ],
        check=False,
        env={**os.environ, "GH_TOKEN": gh_token},
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None, None, False
    parts = result.stdout.strip().split("\t")
    if len(parts) != 4:
        return None, None, False
    branch, owner, repo, state = parts
    if not branch or not owner or not repo or "null" in (branch, owner, repo):
        return None, None, False
    if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
        print(f"Warning: Invalid owner/repo from PR metadata: {owner}/{repo}")
        return None, None, False
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return branch, clone_url, state == "MERGED"


def _push_docs_pr_for_merged(pr_number, docs_branch, docs_files, gh_token):
    """Push the docs branch and create/update a PR for a merged source PR.

    The caller is responsible for switching to the docs branch and
    committing the changes before calling this function.

    Returns the docs PR URL on success (empty string if URL could not
    be extracted), or None on failure.
    """
    base_branch = os.environ.get("DOCS_BASE_BRANCH") or "main"
    try:
        run_command_safe(
            ["git", "push", "--set-upstream", "origin", docs_branch, "--force-with-lease"],
            check=True,
        )
        pr_body = (
            f"Documentation updates based on merged PR #{pr_number}.\n\n"
            "Files updated:\n"
            + "\n".join([f"- `{f}`" for f in docs_files])
            + "\n\n*Assisted by code-to-docs AI*"
        )
        check_pr = run_command_safe(
            ["gh", "pr", "list", "--head", docs_branch, "--state", "open", "--json", "number,url"],
            check=False,
            env={**os.environ, "GH_TOKEN": gh_token},
        )
        existing_pr = check_pr.stdout.strip() if check_pr.returncode == 0 else "[]"
        if existing_pr and existing_pr != "[]":
            try:
                pr_data = json.loads(existing_pr)[0]
                url = pr_data.get("url", "")
                pr_num = pr_data.get("number")
            except (ValueError, IndexError, KeyError):
                print(f"Warning: Could not parse existing PR data for {docs_branch}")
                return None
            if pr_num:
                run_command_safe(
                    ["gh", "pr", "edit", str(pr_num), "--body", pr_body],
                    check=False,
                    env={**os.environ, "GH_TOKEN": gh_token},
                )
            print(f"✅ Updated existing docs PR (branch {docs_branch})")
            return url

        create_result = run_command_safe(
            [
                "gh",
                "pr",
                "create",
                "--title",
                f"{get_pr_title_prefix()}docs: update documentation from PR #{pr_number}",
                "--body",
                pr_body,
                "--base",
                base_branch,
                "--head",
                docs_branch,
            ],
            check=True,
            env={**os.environ, "GH_TOKEN": gh_token},
        )
        url = create_result.stdout.strip() or ""
        print(f"✅ Created docs PR (branch {docs_branch})")
        return url
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to create docs PR: {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate changes without writing files or pushing PR",
    )
    parser.add_argument(
        "--use-index",
        action="store_true",
        default=True,
        help="Use semantic indexes for faster file discovery (default: True)",
    )
    parser.add_argument(
        "--no-index", action="store_true", help="Disable index-based optimization, use full scan"
    )
    parser.add_argument("--build-index", action="store_true", help="Build/rebuild indexes and exit")
    parser.add_argument(
        "--parallel-updates",
        action="store_true",
        default=True,
        help="Generate updates in parallel (default: True)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Max parallel workers for update generation (default: 5)",
    )
    args = parser.parse_args()

    # Log context budget once at startup
    budget = get_max_context_chars()
    raw = os.environ.get("MAX_CONTEXT_CHARS", "")
    source = "MAX_CONTEXT_CHARS" if raw else "default"
    print(f"Context budget: {budget:,} chars (from {source})")

    # Initialize token usage tracking
    cost_input = os.environ.get("COST_PER_1M_INPUT", "")
    cost_output = os.environ.get("COST_PER_1M_OUTPUT", "")
    usage_tracker = UsageTracker(
        cost_per_1m_input=float(cost_input) if cost_input else None,
        cost_per_1m_output=float(cost_output) if cost_output else None,
    )

    # Initialize structured run log
    debug_artifacts = os.environ.get("DEBUG_ARTIFACTS", "false").lower() == "true"
    run_log = RunLog(include_prompts=debug_artifacts)

    # Load persistent style guidelines from the base branch so the AI always
    # uses the repo's current style config, even if the PR branch predates it.
    style_guidelines = load_style_config_from_branch()

    # Handle --build-index mode
    if args.build_index:
        print("Building documentation indexes...")
        if not setup_docs_environment():
            print("Failed to set up docs environment")
            return
        result = build_all_indexes(force=True)
        print(f"Index build complete: {result['status']}")
        return

    # Detect which command was used
    comment_body = os.environ.get("COMMENT_BODY", "")

    # === [review-feature] — parse Jira key early, run feature analysis later ===
    feature_mode = "[review-feature]" in comment_body.lower()
    feature_issue_key = None
    feature_instructions = ""
    feature_section = ""

    if feature_mode:
        feature_issue_key, feature_instructions = parse_feature_command(comment_body)
        if not feature_issue_key:
            print("Error: Could not parse Jira issue key from comment.")
            pr_number = os.environ.get("PR_NUMBER", "unknown")
            if pr_number and pr_number != "unknown":
                msg = (
                    "## 🔍 Spec vs Code Analysis\n\n"
                    "Could not parse Jira issue key from your comment.\n\n"
                    "Usage: <code>&#91;review-feature] PROJ-123</code>"
                )
                msg_file = Path("/tmp/missing_key.md")
                msg_file.write_text(msg, encoding="utf-8")
                gh_token = os.environ.get("GH_TOKEN")
                if gh_token:
                    run_command_safe(
                        ["gh", "pr", "comment", str(pr_number), "--body-file", str(msg_file)],
                        env={**os.environ, "GH_TOKEN": gh_token},
                        check=False,
                    )
            return

        # Validate Jira credentials
        jira_vars = ["JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"]
        missing_jira = [v for v in jira_vars if not os.environ.get(v)]
        if missing_jira:
            print(f"Error: Missing Jira credentials: {', '.join(missing_jira)}")
            pr_number = os.environ.get("PR_NUMBER", "unknown")
            if pr_number and pr_number != "unknown":
                missing_list = "\n".join([f"- `{v}`" for v in missing_jira])
                msg = (
                    "## 🔍 Spec vs Code Analysis\n\n"
                    f"Could not run feature analysis for `{feature_issue_key}`. "
                    "The following secrets are missing:\n\n"
                    f"{missing_list}\n\n"
                    "Please add them in **Settings → Secrets → Actions** and comment "
                    "<code>&#91;review-feature] PROJ-123</code> again.\n\n"
                    "You can also use <code>&#91;review-docs]</code> or <code>&#91;update-docs]</code> which don't require Jira credentials.\n\n"
                    "For setup details, see the [configuration guide](https://github.com/redhat-community-ai-tools/code-to-docs#2-configure-secrets)."
                )
                msg_file = Path("/tmp/missing_secrets.md")
                msg_file.write_text(msg, encoding="utf-8")
                gh_token = os.environ.get("GH_TOKEN")
                if gh_token:
                    run_command_safe(
                        ["gh", "pr", "comment", str(pr_number), "--body-file", str(msg_file)],
                        env={**os.environ, "GH_TOKEN": gh_token},
                        check=False,
                    )
            return

        print(f"Feature review enabled for: {feature_issue_key}")

    # Determine mode based on comment
    review_mode = "[review-docs]" in comment_body.lower()
    update_mode = "[update-docs]" in comment_body.lower()

    if not review_mode and not update_mode:
        if feature_mode:
            review_mode = True
        else:
            update_mode = True

    # Determine if we should use indexes
    use_index = args.use_index and not args.no_index

    print(
        f"Mode: {'Review' if review_mode and not update_mode else 'Update' if update_mode and not review_mode else 'Review + Update'}"
    )
    print(f"Optimization: {'Index-based' if use_index else 'Full scan'}")

    diff = get_diff()
    if not diff:
        print("No changes detected.")
        return

    # Check if diff is too large for the context budget
    # The pipeline needs room for prompt templates (~2K), file previews, and doc content.
    # If the diff alone takes 90%+ of the budget, there's not enough room.
    diff_ratio = len(diff) / budget
    if diff_ratio > 0.9:
        print(
            f"Error: Diff is too large ({len(diff):,} chars) for the context budget ({budget:,} chars). "
            f"The diff uses {diff_ratio:.0%} of the budget, leaving insufficient room for documentation content."
        )
        print("Options:")
        print(f"  - Increase MAX_CONTEXT_CHARS (current: {budget:,})")
        print("  - Split the PR into smaller changes")
        return

    # Get commit info before switching to docs repo
    commit_info = get_commit_info()
    if commit_info:
        print(f"Source repository: {commit_info['repo_url']}")
        print(f"Latest commit: {commit_info['short_hash']}")

    # Get PR number and description for context
    pr_number = os.environ.get("PR_NUMBER", "unknown")
    pr_description = _get_pr_description(pr_number)

    # === FEATURE ANALYSIS (before docs env setup, since it uses MCP not docs repo) ===
    if feature_mode and feature_issue_key:
        print("Fetching Jira context via MCP...")
        jira_context = fetch_jira_context_sync(feature_issue_key)

        if jira_context["error"]:
            print(f"Error fetching Jira data: {jira_context['error']}")
            feature_section = (
                "\n\n---\n\n## 🔍 Feature Coverage\n\n"
                f"**Error:** Could not fetch Jira ticket {feature_issue_key}.\n\n"
                f"`{jira_context['error']}`\n\n"
                f"Please check that the issue key is correct and that the "
                f"Jira credentials have access to this ticket."
            )
        else:
            print(f"Ticket: {jira_context['summary']}")
            print(f"Spec docs found: {len(jira_context['spec_docs'])}")
            if jira_context["inaccessible_links"]:
                print(f"Inaccessible links: {len(jira_context['inaccessible_links'])}")

            print("Running feature coverage analysis...")
            analysis = analyze_feature_coverage(
                diff,
                jira_context,
                get_client(),
                get_model_name(),
                user_instructions=feature_instructions or "",
            )
            feature_section = format_feature_review_section(
                feature_issue_key,
                jira_context["summary"],
                analysis,
                jira_context["inaccessible_links"],
            )

    # === INTERACTIVE REVIEW: check for previous review when [update-docs] ===
    previous_review = None
    user_instructions = ""
    file_instructions = {}
    if update_mode and not review_mode:
        user_instructions, file_instructions = parse_update_instructions(comment_body)

        print("Checking for previous interactive review comment...")
        previous_review = parse_previous_review(pr_number)

        if previous_review["review_found"]:
            suggested = len(previous_review["accepted_files"]) + len(
                previous_review["rejected_files"]
            )
            accepted = len(previous_review["accepted_files"])
            if suggested > 0:
                print(f"Acceptance rate: suggested={suggested} accepted={accepted}")

            if previous_review["review_commit"] and commit_info:
                if previous_review["review_commit"] != commit_info["short_hash"]:
                    print(
                        f"Warning: Review was based on commit {previous_review['review_commit']}, "
                        f"current HEAD is {commit_info['short_hash']}. "
                        "Consider re-running [review-docs] for fresh analysis."
                    )

            if not previous_review["accepted_files"]:
                print("All files were unchecked in the review. No updates to apply.")
                post_review_comment(
                    [],
                    pr_number,
                    commit_info,
                    include_full_content=False,
                    feature_section=feature_section,
                )
                return

    if not setup_docs_environment():
        print("Failed to set up docs environment")
        return

    # For merged PRs in same-repo mode, switch to main's docs content
    # BEFORE discovery/generation so the pipeline runs against main's docs.
    # update_mode creates a branch (needs to push); review_mode does a
    # read-only checkout of the docs subfolder.
    docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
    pr_merged = False
    is_fork = False
    push_failed = False
    docs_pr_url = None
    docs_pr_failed = False
    docs_branch = None
    pr_branch_info = None
    if (update_mode or review_mode) and docs_subfolder:
        pr_branch_info = _resolve_pr_push_target(pr_number)
        _, _, pr_merged = pr_branch_info
        if pr_merged:
            base_branch = os.environ.get("DOCS_BASE_BRANCH") or "main"
            try:
                os.chdir("..")
                run_command_safe(["git", "fetch", "origin", base_branch], check=False)
                if update_mode:
                    docs_branch = f"docs/update-from-pr-{pr_number}"
                    run_command_safe(["git", "fetch", "origin", docs_branch], check=False)
                    run_command_safe(
                        ["git", "checkout", "-B", docs_branch, f"origin/{base_branch}"],
                        check=True,
                    )
                    print(f"Switched to {docs_branch} (based on {base_branch}) for merged PR")
                else:
                    run_command_safe(
                        ["git", "checkout", f"origin/{base_branch}", "--", docs_subfolder],
                        check=True,
                    )
                    print(f"Checked out {docs_subfolder} from {base_branch} for merged PR review")
                os.chdir(docs_subfolder)
            except (subprocess.CalledProcessError, OSError) as e:
                print(
                    f"Warning: Failed to checkout docs from {base_branch}: {sanitize_output(str(e))}"
                )
                pr_merged = False
                try:
                    os.chdir(docs_subfolder)
                except OSError:
                    print("Error: Could not restore working directory, aborting")
                    return

    if not pr_merged:
        checkout_docs_from_base_branch()

    # === FILE DISCOVERY ===
    if previous_review and previous_review["review_found"] and previous_review["accepted_files"]:
        relevant_files = previous_review["accepted_files"]
        print(
            f"Using {len(relevant_files)} file(s) accepted from previous review: {relevant_files}"
        )
        if previous_review["rejected_files"]:
            print(
                f"Skipping {len(previous_review['rejected_files'])} rejected file(s): {previous_review['rejected_files']}"
            )
    else:
        if use_index:
            print("Using optimized index-based file discovery...")
            relevant_files = find_relevant_files_optimized(diff)

            if relevant_files is None:
                print("Index-based discovery requested full scan, falling back...")
                use_index = False

        if not use_index:
            file_previews = get_file_content_or_summaries()

            if not file_previews:
                print("No documentation files found to process.")
                return

            print("Asking AI for relevant files...")
            relevant_files = ask_ai_for_relevant_files(diff, file_previews)

    if not relevant_files:
        print("AI did not suggest any files.")
        if review_mode or update_mode or feature_mode:
            post_review_comment(
                [],
                pr_number,
                commit_info,
                include_full_content=False,
                feature_section=feature_section,
            )
        return

    print("Files selected for processing:", relevant_files)

    # === GENERATE UPDATES ===
    files_with_content = []
    modified_files = []

    if args.parallel_updates and len(relevant_files) > 1:
        print(f"Generating updates in parallel (max {args.max_workers} workers)...")
        files_with_content = generate_updates_parallel(
            diff,
            relevant_files,
            max_workers=args.max_workers,
            user_instructions=user_instructions,
            file_instructions=file_instructions,
            style_guidelines=style_guidelines,
            pr_description=pr_description,
            usage_tracker=usage_tracker,
        )

        for file_path, _current, updated in files_with_content:
            if update_mode and not args.dry_run:
                print(f"Updating {file_path}...")
                if overwrite_file(file_path, updated):
                    modified_files.append(file_path)
            elif args.dry_run:
                print(f"[Dry Run] Would update {file_path}")
    else:
        for file_path in relevant_files:
            current = load_full_content(file_path)
            if not current:
                continue

            print(f"Checking if {file_path} needs an update...")
            updated = ask_ai_for_updated_content(
                diff,
                file_path,
                current,
                user_instructions=user_instructions,
                file_instructions=file_instructions,
                style_guidelines=style_guidelines,
                pr_description=pr_description,
                usage_tracker=usage_tracker,
            )

            if updated.strip() == "NO_UPDATE_NEEDED":
                print(f"No update needed for {file_path}")
                continue

            files_with_content.append((file_path, current, updated))

            if update_mode and not args.dry_run:
                print(f"Updating {file_path}...")
                if overwrite_file(file_path, updated):
                    modified_files.append(file_path)
            elif args.dry_run:
                print(f"[Dry Run] Would update {file_path}")

    # Handle different modes
    if files_with_content:
        if (review_mode or feature_mode) and not args.dry_run:
            print(f"Posting review comment on PR #{pr_number}...")
            post_review_comment(
                files_with_content,
                pr_number,
                commit_info,
                include_full_content=False,
                feature_section=feature_section,
            )

        if update_mode and modified_files:
            if args.dry_run:
                print("[Dry Run] Would push and open PR for the following files:")
                for f in modified_files:
                    print(f"- {f}")
            else:
                if docs_subfolder:
                    print("Same-repo scenario: committing docs...")
                    os.chdir("..")
                    docs_files = [
                        f"{docs_subfolder}/{f}" if not f.startswith(docs_subfolder) else f
                        for f in modified_files
                    ]

                    commit_msg = (
                        f"{get_pr_title_prefix()}docs: update documentation based on code changes"
                    )
                    if commit_info:
                        commit_msg += "\n\nAssisted-by: code-to-docs AI"

                    run_command_safe(["git", "add"] + docs_files, check=True)
                    run_command_safe(["git", "commit", "-m", commit_msg], check=True)

                    gh_token = os.environ.get("GH_TOKEN")
                    if not gh_token:
                        print(
                            "Warning: GH_TOKEN not set, doc updates committed locally but not pushed"
                        )
                    elif pr_merged:
                        docs_pr_url = _push_docs_pr_for_merged(
                            pr_number, docs_branch, docs_files, gh_token
                        )
                        if docs_pr_url is None:
                            docs_pr_failed = True
                    else:
                        pr_branch, pr_repo_url, _ = pr_branch_info or _resolve_pr_push_target(
                            pr_number
                        )
                        if not pr_branch:
                            print("Warning: Could not resolve PR branch name, cannot push")
                            push_failed = True
                        else:
                            origin_url = run_command_safe(
                                ["git", "config", "--get", "remote.origin.url"], check=False
                            )
                            current_origin = (
                                origin_url.stdout.strip() if origin_url.returncode == 0 else ""
                            )

                            is_fork = (
                                pr_repo_url
                                and current_origin
                                and _normalize_github_url(pr_repo_url)
                                != _normalize_github_url(current_origin)
                            )

                            if is_fork:
                                print(
                                    "Fork PR detected — cannot push directly. "
                                    "Suggested changes will be shown in the PR comment."
                                )
                            else:
                                try:
                                    run_command_safe(
                                        [
                                            "git",
                                            "push",
                                            "origin",
                                            f"HEAD:refs/heads/{pr_branch}",
                                        ],
                                        check=True,
                                    )
                                    print(f"✅ Pushed doc updates to PR branch ({pr_branch})")
                                except subprocess.CalledProcessError as e:
                                    print(
                                        f"Warning: Failed to push doc updates: {e}. "
                                        "Check that GH_TOKEN has contents:write permission."
                                    )
                                    push_failed = True
                else:
                    print("Separate-repo scenario: creating PR...")
                    push_and_open_pr(modified_files, commit_info)

            # Post confirmation comment for [update-docs]
            if update_mode and modified_files and not args.dry_run:
                confirm_parts = []
                show_as_suggestion = is_fork or push_failed
                if show_as_suggestion:
                    confirm_parts.append("## 📝 Suggested Documentation Changes")
                else:
                    confirm_parts.append("## 📚 Documentation Update")
                confirm_parts.append("")
                if modified_files:
                    if show_as_suggestion:
                        confirm_parts.append(
                            f"Suggested updates for **{len(modified_files)} file(s)**:"
                        )
                    elif previous_review and previous_review["review_found"]:
                        confirm_parts.append(
                            f"Updated **{len(modified_files)} file(s)** based on your review selections:"
                        )
                    else:
                        confirm_parts.append(f"Updated **{len(modified_files)} file(s)**:")
                    confirm_parts.append("")
                    marker = "📄" if show_as_suggestion else "✅"
                    for f in modified_files:
                        confirm_parts.append(f"- {marker} `{f}`")
                if previous_review and previous_review.get("rejected_files"):
                    confirm_parts.append("")
                    confirm_parts.append(
                        f"Skipped **{len(previous_review['rejected_files'])} file(s)** (unchecked):"
                    )
                    confirm_parts.append("")
                    for f in previous_review["rejected_files"]:
                        confirm_parts.append(f"- ⏭️ `{f}`")
                if modified_files:
                    confirm_parts.append("")
                    confirm_parts.append("### 📄 Changes")
                    confirm_parts.append("")
                    for file_path, original, updated in files_with_content:
                        if file_path in modified_files:
                            confirm_parts.append(f"#### `{file_path}`")
                            confirm_parts.append("")
                            confirm_parts.append("<details>")
                            confirm_parts.append("<summary><b>View diff</b></summary>")
                            confirm_parts.append("")
                            diff_lines = list(
                                difflib.unified_diff(
                                    original.splitlines(keepends=True),
                                    updated.splitlines(keepends=True),
                                    fromfile=f"a/{file_path}",
                                    tofile=f"b/{file_path}",
                                    n=3,
                                )
                            )
                            if diff_lines:
                                confirm_parts.append("```diff")
                                confirm_parts.append("".join(diff_lines))
                                confirm_parts.append("```")
                            confirm_parts.append("")
                            confirm_parts.append("</details>")
                            confirm_parts.append("")
                    docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
                    if docs_subfolder and pr_merged and docs_pr_failed:
                        confirm_parts.append(
                            "Failed to create a docs PR. The changes are shown above for manual application."
                        )
                    elif docs_subfolder and pr_merged and docs_pr_url:
                        confirm_parts.append(f"A docs PR has been created: {docs_pr_url}")
                    elif docs_subfolder and pr_merged:
                        confirm_parts.append("A docs PR has been created with these changes.")
                    elif docs_subfolder and is_fork:
                        confirm_parts.append(
                            "This is a fork PR, so I can't push changes directly. "
                            "The suggested changes are shown above for you to apply.\n\n"
                            "Once this PR is merged, comment <code>&#91;update-docs]</code> again "
                            "and I'll create a docs PR with these updates."
                        )
                    elif docs_subfolder and push_failed:
                        confirm_parts.append(
                            "Failed to push doc updates to this PR. "
                            "The suggested changes are shown above for manual application."
                        )
                    elif docs_subfolder:
                        confirm_parts.append("Doc updates have been committed to this PR.")
                    else:
                        confirm_parts.append(
                            "A docs PR has been created/updated with these changes."
                        )
                if usage_tracker.has_records:
                    confirm_parts.append("")
                    confirm_parts.append(usage_tracker.format_summary())
                confirm_body = "\n".join(confirm_parts)
                confirm_file = Path("/tmp/update_confirm.md")
                confirm_file.write_text(confirm_body, encoding="utf-8")
                gh_token = os.environ.get("GH_TOKEN")
                if gh_token:
                    run_command_safe(
                        ["gh", "pr", "comment", str(pr_number), "--body-file", str(confirm_file)],
                        env={**os.environ, "GH_TOKEN": gh_token},
                        check=False,
                    )

        elif update_mode and not modified_files and not args.dry_run:
            print("All documentation is already up to date — no PR created.")
    else:
        if (review_mode or update_mode or feature_mode) and not args.dry_run:
            print("Posting comment that no updates are needed...")
            post_review_comment(
                [],
                pr_number,
                commit_info,
                include_full_content=False,
                feature_section=feature_section,
            )
        else:
            print("All documentation is already up to date — no PR created.")

    if run_log.has_entries:
        print(f"Run log written to: {run_log.path}")


if __name__ == "__main__":
    main()
