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

import os
import subprocess
import argparse
import difflib
from pathlib import Path

from config import get_client, get_model_name, get_branch_name, get_max_context_chars
from github_ops import get_diff, get_commit_info, setup_docs_environment, push_and_open_pr
from discovery import find_relevant_files_optimized, ask_ai_for_relevant_files, get_file_content_or_summaries
from generation import generate_updates_parallel, load_full_content, ask_ai_for_updated_content, overwrite_file
from comments import (
    parse_update_instructions,
    parse_previous_review,
    post_review_comment,
)
from security_utils import sanitize_output, run_command_safe
from jira_integration import (
    parse_feature_command,
    fetch_jira_context_sync,
    analyze_feature_coverage,
    format_feature_review_section,
)
from doc_index import build_all_indexes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulate changes without writing files or pushing PR")
    parser.add_argument("--use-index", action="store_true", default=True, help="Use semantic indexes for faster file discovery (default: True)")
    parser.add_argument("--no-index", action="store_true", help="Disable index-based optimization, use full scan")
    parser.add_argument("--build-index", action="store_true", help="Build/rebuild indexes and exit")
    parser.add_argument("--parallel-updates", action="store_true", default=True, help="Generate updates in parallel (default: True)")
    parser.add_argument("--max-workers", type=int, default=5, help="Max parallel workers for update generation (default: 5)")
    args = parser.parse_args()

    # Log context budget once at startup
    budget = get_max_context_chars()
    raw = os.environ.get("MAX_CONTEXT_CHARS", "")
    source = "MAX_CONTEXT_CHARS" if raw else "default"
    print(f"Context budget: {budget:,} chars (from {source})")

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
                    "Usage: `[review-feature] PROJ-123`"
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
                    "`[review-feature] PROJ-123` again.\n\n"
                    "You can also use `[review-docs]` or `[update-docs]` which don't require Jira credentials.\n\n"
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

    print(f"Mode: {'Review' if review_mode and not update_mode else 'Update' if update_mode and not review_mode else 'Review + Update'}")
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
        print(f"Error: Diff is too large ({len(diff):,} chars) for the context budget ({budget:,} chars). "
              f"The diff uses {diff_ratio:.0%} of the budget, leaving insufficient room for documentation content.")
        print(f"Options:")
        print(f"  - Increase MAX_CONTEXT_CHARS (current: {budget:,})")
        print(f"  - Split the PR into smaller changes")
        return

    # Get commit info before switching to docs repo
    commit_info = get_commit_info()
    if commit_info:
        print(f"Source repository: {commit_info['repo_url']}")
        print(f"Latest commit: {commit_info['short_hash']}")

    # Get PR number for posting comments
    pr_number = os.environ.get("PR_NUMBER", "unknown")

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
                diff, jira_context, get_client(), get_model_name(),
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
            if previous_review["review_commit"] and commit_info:
                if previous_review["review_commit"] != commit_info["short_hash"]:
                    print(
                        f"Warning: Review was based on commit {previous_review['review_commit']}, "
                        f"current HEAD is {commit_info['short_hash']}. "
                        "Consider re-running [review-docs] for fresh analysis."
                    )

            if not previous_review["accepted_files"]:
                print("All files were unchecked in the review. No updates to apply.")
                post_review_comment([], pr_number, commit_info, include_full_content=False, feature_section=feature_section)
                return

    if not setup_docs_environment():
        print("Failed to set up docs environment")
        return

    # === FILE DISCOVERY ===
    if previous_review and previous_review["review_found"] and previous_review["accepted_files"]:
        relevant_files = previous_review["accepted_files"]
        print(f"Using {len(relevant_files)} file(s) accepted from previous review: {relevant_files}")
        if previous_review["rejected_files"]:
            print(f"Skipping {len(previous_review['rejected_files'])} rejected file(s): {previous_review['rejected_files']}")
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
            post_review_comment([], pr_number, commit_info, include_full_content=False, feature_section=feature_section)
        return

    print("Files selected for processing:", relevant_files)

    # === GENERATE UPDATES ===
    files_with_content = []
    modified_files = []

    if args.parallel_updates and len(relevant_files) > 1:
        print(f"Generating updates in parallel (max {args.max_workers} workers)...")
        files_with_content = generate_updates_parallel(
            diff, relevant_files, max_workers=args.max_workers,
            user_instructions=user_instructions, file_instructions=file_instructions
        )

        for file_path, current, updated in files_with_content:
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
                diff, file_path, current,
                user_instructions=user_instructions, file_instructions=file_instructions
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
            post_review_comment(files_with_content, pr_number, commit_info, include_full_content=False, feature_section=feature_section)

        if update_mode and modified_files:
            if args.dry_run:
                print("[Dry Run] Would push and open PR for the following files:")
                for f in modified_files:
                    print(f"- {f}")
            else:
                docs_subfolder = os.environ.get("DOCS_SUBFOLDER")
                if docs_subfolder:
                    print("Same-repo scenario: preparing for PR creation...")
                    os.chdir("..")
                    branch_name = get_branch_name(os.environ.get("PR_NUMBER"))
                    try:
                        run_command_safe(["git", "checkout", "-b", branch_name], check=True)
                    except subprocess.CalledProcessError:
                        run_command_safe(["git", "checkout", branch_name], check=True)
                    docs_files = [f"{docs_subfolder}/{f}" if not f.startswith(docs_subfolder) else f for f in modified_files]
                    push_and_open_pr(docs_files, commit_info)
                else:
                    print("Separate-repo scenario: creating PR...")
                    push_and_open_pr(modified_files, commit_info)

            # Post confirmation comment for [update-docs]
            if update_mode and modified_files and not args.dry_run:
                confirm_parts = []
                confirm_parts.append("## 📚 Documentation Update")
                confirm_parts.append("")
                if modified_files:
                    if previous_review and previous_review["review_found"]:
                        confirm_parts.append(f"Updated **{len(modified_files)} file(s)** based on your review selections:")
                    else:
                        confirm_parts.append(f"Updated **{len(modified_files)} file(s)**:")
                    confirm_parts.append("")
                    for f in modified_files:
                        confirm_parts.append(f"- ✅ `{f}`")
                if previous_review and previous_review.get("rejected_files"):
                    confirm_parts.append("")
                    confirm_parts.append(f"Skipped **{len(previous_review['rejected_files'])} file(s)** (unchecked):")
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
                            diff_lines = list(difflib.unified_diff(
                                original.splitlines(keepends=True),
                                updated.splitlines(keepends=True),
                                fromfile=f"a/{file_path}",
                                tofile=f"b/{file_path}",
                                n=3,
                            ))
                            if diff_lines:
                                confirm_parts.append("```diff")
                                confirm_parts.append("".join(diff_lines))
                                confirm_parts.append("```")
                            confirm_parts.append("")
                            confirm_parts.append("</details>")
                            confirm_parts.append("")
                    confirm_parts.append("A docs PR has been created/updated with these changes.")
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
            post_review_comment([], pr_number, commit_info, include_full_content=False, feature_section=feature_section)
        else:
            print("All documentation is already up to date — no PR created.")

if __name__ == "__main__":
    main()
