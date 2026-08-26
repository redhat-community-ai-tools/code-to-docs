# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.2] - 2026-08-26

### Fixed

- Normalize `DOCS_SUBFOLDER` trailing slash and resolve paths with
  `os.path.normpath` before comparing against the current working directory
- Clean up stale semantic indexes when their documentation files are deleted,
  fetching the base branch ref for accurate change detection
- Build git `ls-tree` / `cat-file` / `show` pathspecs relative to the current
  working directory using `git rev-parse --show-prefix`

## [0.1.0] - 2026-08-16

Initial tagged release. The action has been in use since September 2025;
this release captures the current feature set for stable pinning.

### Added

- AI-powered documentation review and update via PR comments
  (`[review-docs]`, `[update-docs]`)
- Spec-vs-code gap analysis via Jira integration (`[review-feature]`)
- Support for Markdown, AsciiDoc, and reStructuredText doc formats
- Same-repo and separate-docs-repo configurations
- Semantic folder indexes for faster file discovery
- Persistent style guidelines via `.code-to-docs/style.md`
- Repository configuration via `.code-to-docs/config.json`
- Interactive review with checkboxes for accepting/rejecting file suggestions
- Fork PR detection with suggested-changes fallback
- Per-file and global reviewer instructions in `[update-docs]` comments
- CI pipeline with ruff linting, formatting, and 60% coverage threshold
