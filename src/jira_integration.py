"""
Jira integration via mcp-atlassian for the [review-feature] flow.

Connects to Jira (and optionally Confluence) using the mcp-atlassian MCP server
in read-only mode. Fetches ticket data, linked documents (Confluence pages,
Google Docs), and produces a feature coverage analysis using an OpenAI-compatible LLM.
"""

import os
import re
import json
import asyncio
import shutil

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import get_max_context_chars, check_context_error
from security_utils import sanitize_output, run_command_safe


# Google Docs URL patterns
GDOC_PATTERN = re.compile(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")
GSLIDES_PATTERN = re.compile(r"https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)")
GSHEETS_PATTERN = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)")


def _build_mcp_env():
    """Build environment variables for the mcp-atlassian server."""
    env = {
        "READ_ONLY_MODE": "true",
        "JIRA_URL": os.environ["JIRA_URL"],
        "JIRA_USERNAME": os.environ["JIRA_USERNAME"],
        "JIRA_API_TOKEN": os.environ["JIRA_API_TOKEN"],
    }

    # Confluence uses the same credentials by default
    confluence_url = os.environ.get("CONFLUENCE_URL")
    if confluence_url:
        env["CONFLUENCE_URL"] = confluence_url
        env["CONFLUENCE_USERNAME"] = os.environ.get(
            "CONFLUENCE_USERNAME", os.environ["JIRA_USERNAME"]
        )
        env["CONFLUENCE_API_TOKEN"] = os.environ.get(
            "CONFLUENCE_API_TOKEN", os.environ["JIRA_API_TOKEN"]
        )

    return env


def _extract_text(content_blocks):
    """Extract text from MCP response content blocks."""
    texts = []
    for block in content_blocks:
        if hasattr(block, "text") and block.text:
            texts.append(block.text)
    return "\n".join(texts)


# ─── Google Docs fetching (via gws CLI) ──────────────────────────────────────


def _is_gws_configured():
    """Check if gws CLI is available and Google credentials are configured."""
    # Check if credentials file is set (entrypoint.sh writes GOOGLE_SA_KEY to this file)
    if not os.environ.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"):
        return False
    # Check if gws is installed
    return shutil.which("gws") is not None


def _extract_google_doc_id(url):
    """
    Extract doc ID from a Google Docs/Slides/Sheets URL.

    Returns (doc_id, doc_type) or (None, None).
    """
    for pattern, doc_type in [
        (GDOC_PATTERN, "document"),
        (GSLIDES_PATTERN, "presentation"),
        (GSHEETS_PATTERN, "spreadsheet"),
    ]:
        match = pattern.search(url)
        if match:
            return match.group(1), doc_type
    return None, None


def fetch_google_doc(url):
    """
    Fetch content from a Google Docs/Slides/Sheets URL using gws CLI.

    Args:
        url: Google Docs URL

    Returns:
        tuple: (content, title, error). If error is set, content/title may be empty.
    """
    doc_id, doc_type = _extract_google_doc_id(url)
    if not doc_id or not doc_type:
        return "", "", "Not a valid Google Docs/Slides/Sheets URL"

    # Determine the MIME type for export
    if doc_type == "spreadsheet":
        mime_type = "text/csv"
    else:
        mime_type = "text/plain"

    # gws only allows output within the current directory — use /app to avoid workspace issues
    original_cwd = os.getcwd()
    os.chdir("/app")
    output_file = f"gws-export-{doc_id[:8]}.txt"

    try:
        result = run_command_safe(
            [
                "gws", "drive", "files", "export",
                "--params", json.dumps({"fileId": doc_id, "mimeType": mime_type}),
                "--output", output_file,
            ],
            check=False,
        )

        if result.returncode != 0:
            stderr = result.stderr or ""
            if "403" in stderr or "permission" in stderr.lower():
                return "", "", "Permission denied — ensure the doc is shared with the service account"
            if "file not found" in stderr.lower() or f"not found: {doc_id}" in stderr.lower():
                return "", "", "Document not found — ensure the doc is shared with the service account"
            return "", "", f"gws export failed (exit code {result.returncode})"

        # gws saves content to the output file
        # Check if it saved to our specified path or its default (download.txt)
        if os.path.exists(output_file):
            content = open(output_file, encoding="utf-8").read()
        elif os.path.exists("download.txt"):
            content = open("download.txt", encoding="utf-8").read()
            os.remove("download.txt")
        else:
            # Try parsing stdout for the saved_file path
            try:
                meta = json.loads(result.stdout.strip())
                saved = meta.get("saved_file", "")
                if saved and os.path.exists(saved):
                    content = open(saved, encoding="utf-8").read()
                    os.remove(saved)
                else:
                    return "", "", "gws export succeeded but output file not found"
            except (json.JSONDecodeError, TypeError):
                return "", "", "gws export succeeded but could not locate output"

        if not content.strip():
            return "", "", "Empty document"

        # Extract title from first line
        lines = content.strip().split("\n")
        title = lines[0].strip() if lines else "Untitled"
        if len(title) > 100:
            title = title[:97] + "..."

        return content, title, None
    except Exception as e:
        return "", "", f"Failed to fetch: {sanitize_output(str(e))}"
    finally:
        if os.path.exists(output_file):
            os.remove(output_file)
        os.chdir(original_cwd)


# ─── Link detection ──────────────────────────────────────────────────────────


def _find_all_links(text):
    """
    Find all URLs in text and categorize them.

    Returns:
        dict with keys:
            confluence_page_ids (list[str]): Confluence page IDs
            google_docs_urls (list[str]): Google Docs/Slides/Sheets URLs
            other_urls (list[str]): Other URLs
    """
    links = {
        "confluence_page_ids": [],
        "google_docs_urls": [],
        "other_urls": [],
    }

    # Confluence page IDs
    for match in re.finditer(r'/wiki/spaces/[^/]+/pages/(\d+)', text):
        links["confluence_page_ids"].append(match.group(1))
    for match in re.finditer(r'pageId=(\d+)', text):
        page_id = match.group(1)
        if page_id not in links["confluence_page_ids"]:
            links["confluence_page_ids"].append(page_id)

    # Google Docs URLs
    seen_gdoc_urls = set()
    for pattern in [GDOC_PATTERN, GSLIDES_PATTERN, GSHEETS_PATTERN]:
        for match in pattern.finditer(text):
            url = match.group(0)
            if url not in seen_gdoc_urls:
                seen_gdoc_urls.add(url)
                links["google_docs_urls"].append(url)

    # Other URLs (skip already-matched ones and known non-doc URLs)
    for match in re.finditer(r'https?://[^\s\)\]\"\'<>,]+', text):
        url = match.group(0)
        # Skip Confluence and Google Docs URLs already captured
        if any(p.search(url) for p in [GDOC_PATTERN, GSLIDES_PATTERN, GSHEETS_PATTERN]):
            continue
        if '/wiki/spaces/' in url or 'pageId=' in url:
            continue
        # Skip avatar/profile image URLs
        if "gravatar.com" in url or "avatar" in url.lower():
            continue
        if url not in links["other_urls"]:
            links["other_urls"].append(url)

    return links


# ─── Jira + spec doc fetching ────────────────────────────────────────────────


def parse_feature_command(comment_body):
    """
    Parse the Jira issue key from a [review-feature] comment.

    Supports:
        [review-feature] PROJ-123
        [review-feature] PROJ-123 some extra instructions

    Returns:
        tuple: (issue_key, instructions) or (None, None)
    """
    match = re.search(
        r'\[review-feature\]\s+([A-Z][A-Z0-9]+-\d+)\s*(.*)',
        comment_body,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None

    issue_key = match.group(1).upper()
    instructions = match.group(2).strip()
    return issue_key, instructions


async def fetch_jira_context(issue_key):
    """
    Connect to Jira via MCP and fetch ticket + linked spec docs.

    Fetches:
    - Jira ticket data (description, comments, fields)
    - Linked Confluence pages (via mcp-atlassian)
    - Linked Google Docs (via Google service account, if configured)
    - Flags other links that could not be accessed

    Returns:
        dict with keys:
            issue_key, summary, raw_ticket, spec_docs, inaccessible_links, error
    """
    result = {
        "issue_key": issue_key,
        "summary": "",
        "raw_ticket": "",
        "spec_docs": [],
        "inaccessible_links": [],
        "error": None,
    }

    mcp_env = _build_mcp_env()
    server_params = StdioServerParameters(
        command="uvx",
        args=["mcp-atlassian", "--read-only"],
        env=mcp_env,
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Fetch the Jira ticket
                print(f"Fetching Jira ticket {issue_key}...")
                ticket_result = await session.call_tool(
                    "jira_get_issue",
                    arguments={"issue_key": issue_key},
                )
                ticket_text = _extract_text(ticket_result.content)

                if not ticket_text:
                    result["error"] = f"Empty response for {issue_key}"
                    return result

                if "error" in ticket_text.lower() and "permission" in ticket_text.lower():
                    result["error"] = f"Permission error fetching {issue_key}: {ticket_text[:200]}"
                    return result

                result["raw_ticket"] = ticket_text

                # Extract summary
                try:
                    ticket_data = json.loads(ticket_text)
                    result["summary"] = ticket_data.get("summary", issue_key)
                except (json.JSONDecodeError, TypeError):
                    result["summary"] = issue_key

                # Find all links in ticket data
                links = _find_all_links(ticket_text)
                print(f"Links found — Confluence: {len(links['confluence_page_ids'])}, "
                      f"Google Docs: {len(links['google_docs_urls'])}, "
                      f"Other: {len(links['other_urls'])}")

                # Fetch Confluence pages
                for page_id in links["confluence_page_ids"]:
                    print(f"  Fetching Confluence page: {page_id}...")
                    try:
                        page_result = await session.call_tool(
                            "confluence_get_page",
                            arguments={"page_id": page_id},
                        )
                        page_text = _extract_text(page_result.content)
                        if page_text:
                            try:
                                page_data = json.loads(page_text)
                                title = page_data.get("title", f"Page {page_id}")
                            except (json.JSONDecodeError, TypeError):
                                title = f"Page {page_id}"
                            result["spec_docs"].append({
                                "source": "confluence",
                                "title": title,
                                "content": page_text,
                            })
                    except Exception as e:
                        print(f"  Could not fetch Confluence page {page_id}: {sanitize_output(str(e))}")
                        result["inaccessible_links"].append(
                            f"Confluence page {page_id} (error: {sanitize_output(str(e))})"
                        )

    except Exception as e:
        result["error"] = f"MCP connection failed: {sanitize_output(str(e))}"
        return result

    # Fetch Google Docs (outside MCP session — uses gws CLI)
    if links["google_docs_urls"]:
        if _is_gws_configured():
            for url in links["google_docs_urls"]:
                print(f"  Fetching Google Doc: {url}...")
                content, title, error = fetch_google_doc(url)
                if error:
                    print(f"  Could not fetch Google Doc: {error}")
                    result["inaccessible_links"].append(f"Google Doc ({url}): {error}")
                else:
                    result["spec_docs"].append({
                        "source": "google_docs",
                        "title": title,
                        "content": content,
                    })
        else:
            for url in links["google_docs_urls"]:
                result["inaccessible_links"].append(
                    f"Google Doc ({url}): gws CLI not configured (GOOGLE_SA_KEY not set)"
                )
            print(f"  Skipping {len(links['google_docs_urls'])} Google Docs link(s) — "
                  f"gws CLI not configured")

    # Flag other links
    for url in links["other_urls"]:
        result["inaccessible_links"].append(f"External link ({url}): automated access not supported")

    return result


def fetch_jira_context_sync(issue_key):
    """Synchronous wrapper around fetch_jira_context."""
    return asyncio.run(fetch_jira_context(issue_key))


# ─── Gap analysis ────────────────────────────────────────────────────────────


def analyze_feature_coverage(diff, jira_context, llm_client, model_name, user_instructions=""):
    """
    Ask Gemini to compare PR diff against Jira feature requirements.

    Returns:
        str: Formatted gap analysis text (markdown)
    """
    # Build the feature context
    feature_context = f"## Jira Ticket: {jira_context['issue_key']}\n\n"
    feature_context += f"### Ticket Data\n{jira_context['raw_ticket']}\n\n"

    if jira_context["spec_docs"]:
        feature_context += "### Linked Specification Documents\n\n"
        for doc in jira_context["spec_docs"]:
            feature_context += f"#### [{doc['source']}] {doc['title']}\n{doc['content']}\n\n"

    if jira_context["inaccessible_links"]:
        feature_context += "### Links Found But Not Accessible\n"
        for link in jira_context["inaccessible_links"]:
            feature_context += f"- {link}\n"
        feature_context += "\n"

    # Build prompt template without diff to compute budget
    prompt_template = f"""
You are a senior code reviewer comparing a PR's code changes against the feature
requirements defined in a Jira ticket and its linked specification documents.

{feature_context}

## PR Code Diff
```
{{DIFF_PLACEHOLDER}}
```

## Your Task

Analyze the PR diff and compare it against ALL requirements, acceptance criteria,
and specifications found in the Jira ticket and linked documents.

Produce a structured analysis with these sections:

### 1. Requirements Found
List each distinct requirement or acceptance criterion you identified from the
Jira ticket and linked documents. Number them (REQ-1, REQ-2, etc.).

### 2. Covered Requirements
For each requirement that IS addressed by the PR diff:
- State the requirement ID and description
- Point to the specific code changes that address it
- Note if the coverage is complete or partial

### 3. Missing Requirements
For each requirement that is NOT addressed by the PR diff:
- State the requirement ID and description
- Explain what's missing
- Note if it might be intentionally deferred to a follow-up PR

### 4. Unplanned Changes
List any significant code changes in the diff that don't map to any requirement
in the Jira ticket. These aren't necessarily wrong — just not documented in the
ticket.

### 5. Summary
A brief overall assessment:
- How many requirements are covered vs missing
- Overall coverage percentage
- Any risks or concerns

If some spec documents could not be accessed (listed above), note that the
analysis may be incomplete and recommend manual review of those documents.

Be specific and reference actual code when possible. If the Jira ticket has no
clear requirements (just a vague description), say so and do your best to infer
what the expected deliverables are.
"""

    if user_instructions:
        prompt_template += f"""

## Additional Instructions from Reviewer
{user_instructions}
"""

    total_size = len(prompt_template) + len(diff)
    budget = get_max_context_chars()
    if total_size > budget:
        return (
            f"Error: The combined size of the diff, Jira ticket, spec docs, and prompt "
            f"({total_size:,} chars) exceeds the context budget ({budget:,} chars). "
            f"The analysis would be incomplete.\n\n"
            f"Increase `MAX_CONTEXT_CHARS` to at least {total_size:,} to run this analysis."
        )

    prompt = prompt_template.replace("{DIFF_PLACEHOLDER}", diff)

    try:
        response = llm_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip() or "Error: Empty response"
    except Exception as e:
        check_context_error(e)
        return f"Error generating analysis: {sanitize_output(str(e))}"


# ─── Review comment formatting ───────────────────────────────────────────────


def format_feature_review_section(issue_key, summary, analysis, inaccessible_links=None):
    """
    Format the feature coverage section to append to the existing review comment.

    Returns:
        str: Markdown section for feature coverage
    """
    jira_url = os.environ.get("JIRA_URL", "")
    ticket_url = f"{jira_url}/browse/{issue_key}" if jira_url else ""
    ticket_link = f"[{issue_key}]({ticket_url})" if ticket_url else f"**{issue_key}**"

    parts = []
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("## 🔍 Spec vs Code Analysis")
    parts.append("")
    parts.append(f"**Jira:** {ticket_link} — {summary}")
    parts.append("")
    parts.append(analysis)

    if inaccessible_links:
        parts.append("")
        parts.append("### ⚠️ Documents Not Accessible")
        parts.append("")
        parts.append("The following links were found in the Jira ticket but could not be "
                      "fetched automatically. Manual review recommended:")
        parts.append("")
        for link in inaccessible_links:
            parts.append(f"- {link}")

    return "\n".join(parts)
