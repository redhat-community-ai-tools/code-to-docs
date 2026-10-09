# Release Process

This document explains how to release new versions of the code-to-docs action.

## How It Works

Pushing a semver tag triggers `.github/workflows/release.yaml`, which:

1. Runs the full test suite (lint, format, tests with coverage)
2. Extracts the release notes from `CHANGELOG.md`
3. Creates a GitHub Release
4. Force-updates the moving major tag (e.g. `v0` points to `v0.1.0`)

## Releasing a New Version

### 1. Update CHANGELOG.md

Move items from `[Unreleased]` into a new version section:

```markdown
## [0.2.0] - 2026-09-01

### Added
- New feature description
```

### 2. Bump the version in pyproject.toml

```toml
version = "0.2.0"
```

### 3. Commit, tag, and push

```bash
git add CHANGELOG.md pyproject.toml
git commit -m "chore(release): prepare v0.2.0"
git tag v0.2.0
git push origin main --tags
```

The release workflow handles the rest: it creates the GitHub Release and
moves the `v0` tag.

## Version Numbering

Follow semantic versioning (MAJOR.MINOR.PATCH):

- **MAJOR** (v1.0.0): Breaking changes to action inputs, outputs, or behavior
- **MINOR** (v0.2.0): New features, backward compatible
- **PATCH** (v0.1.1): Bug fixes, backward compatible

## What Users Pin To

- `@v0` receives all compatible updates (recommended)
- `@v0.1.0` is pinned to an exact release
- `@main` tracks unreleased changes (unstable, not recommended)

## Marketplace

After the GitHub Release is created, visit the Release page and click
"Publish this Action to the GitHub Marketplace" if the listing is not yet
automatic. This is a manual step in the GitHub UI.
