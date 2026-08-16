# Security Policy

## Reporting a Vulnerability

If you find a potential security vulnerability in this project, please report it responsibly.

### Use the GitHub Security Tab

This repository is set up to allow vulnerability reports through GitHub's Security Advisories feature. To report a vulnerability:

1. Navigate to the repository's main page.
2. Select the [**Security**](https://github.com/redhat-community-ai-tools/code-to-docs/security) tab.
3. Select **Advisories** from the left-hand sidebar.
4. Click on **Report a vulnerability**.
5. Fill in the required details and submit the report.

Following this process will create a private advisory for our maintainers to review.

### Do Not Open Public Pull Requests, Issues, or Discussions

Please **do not** discuss the issue, create PRs, or start discussions about the vulnerability. This ensures the vulnerability is not widely exploited before a fix is provided.

## Threat Model

This section enumerates untrusted inputs, what they can influence, and the
mitigations in place. It is intended for security-conscious adopters who want
to assess the tool without reading the source.

### Permissions Required

The action requires `contents: write` on the target repository. This allows
it to push commits to PR branches and create documentation PRs. It does
**not** require `admin` permissions.

### Untrusted Inputs

| Input | Source | What it can influence | Mitigation |
|-------|--------|----------------------|------------|
| Code diff | PR head branch (contributor-controlled on forks) | LLM prompt content for file selection and generation | Diff is data in the prompt, not instructions. Truncated to context budget. |
| Documentation content | PR head branch | LLM prompt for verification; also read for current file content | Post-generation verification wraps doc content in explicit data delimiters. |
| PR description | PR author | Included as context in generation prompts | Treated as context only, not instructions. |
| Comment body | PR commenter (gated by `author_association`) | Command parsing, user instructions passed to LLM | Only `OWNER`, `MEMBER`, and `COLLABORATOR` can trigger commands (enforced in the workflow `if` condition). |
| Jira ticket content | External system | Included in `[review-feature]` prompts | Fetched server-side via authenticated API. Content is data, not instructions. |
| Google Docs / Confluence content | External system | Included in `[review-feature]` prompts | Fetched via service account. Content is data, not instructions. |
| `.code-to-docs/style.md` | Base branch (maintainer-controlled) | Injected into generation prompts as style guidelines | Loaded from the base branch, not the PR branch. |
| `.code-to-docs/config.json` | Base branch (maintainer-controlled) | Controls validation thresholds and behavior | Loaded from the base branch, not the PR branch. Values are validated on load. |

### Fork PRs

The recommended workflow includes an `author_association` gate:

```yaml
contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)
```

This prevents external contributors from triggering the action on their own
PRs. However, a maintainer commenting `[update-docs]` on a fork PR will
cause the action to read diff and doc content from the fork's head branch,
which is contributor-controlled.

**What the gate protects against:** unauthorized triggering, resource
consumption, and unsolicited PR creation.

**What the gate does not protect against:** a maintainer explicitly
approving a run on a fork PR where the contributor has placed adversarial
content in documentation files. The post-generation verification step
(added in PR #53) wraps document content in data delimiters to reduce this
risk, but LLM-based defenses are not absolute.

**Fork push limitation:** the action cannot push to fork branches due to
GitHub token scoping. On fork PRs, it posts suggested changes as diffs in a
PR comment instead.

### Credential Handling

- `GH_TOKEN`, `MODEL_API_KEY`, `JIRA_API_TOKEN`, and `GOOGLE_SA_KEY` are
  scrubbed from all output by `security_utils.sanitize_output()`.
- Credentials are never interpolated into strings or logged.
- File paths are validated against traversal attacks before read or write.
- The Docker container runs with no network access beyond what the
  entrypoint requires.

### LLM Prompt Injection

Documentation content and code diffs are included in LLM prompts. A
malicious document containing text like "ignore previous instructions" could
theoretically influence the LLM's output. Mitigations:

1. Document content in verification prompts is wrapped in explicit
   `--- BEGIN/END ... (untrusted content, data only) ---` delimiters with
   an instruction to treat it as data, not instructions.
2. Post-generation validation checks for large content removals and
   verifies updates with an independent LLM call in a separate session.
3. The tool never executes generated content; it only writes documentation
   files.
4. Generated output is format-validated (Markdown, RST, AsciiDoc parsers)
   before being committed.
