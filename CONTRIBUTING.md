# Contributing to Code-to-Docs

Thank you for your interest in contributing! This guide covers the development
workflow so you can get started quickly.

## Development Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/) for package management.

```bash
# Install dependencies (including dev tools)
uv sync --extra dev

# Set up pre-commit hooks
uv run pre-commit install
```

## Running Tests

```bash
# Run tests
uv run pytest -v

# Run tests with coverage
uv run pytest --cov=src --cov-report=term-missing
```

CI enforces a minimum coverage threshold. Check `pyproject.toml` for the current
value.

## Linting and Formatting

```bash
# Lint
uv run ruff check src/ tests/

# Format check
uv run ruff format --check src/ tests/

# Auto-format
uv run ruff format src/ tests/
```

## Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

- `feat(scope): description` for new features
- `fix(scope): description` for bug fixes
- `chore(scope): description` for maintenance
- `docs(scope): description` for documentation-only changes
- `refactor(scope): description` for refactors with no behavior change

Include a commit body explaining *why* the change is needed, not just what it does.

## Pull Requests

- One logical change per PR. If a PR contains multiple independent changes,
  split them.
- All CI checks must pass: lint, format, and tests with coverage.
- Update documentation (`README.md`, `CLAUDE.md`) if your change adds or
  modifies user-facing behavior.
- Add tests for new functionality. Tests are in `tests/` and mirror source
  modules (`test_<module>.py`).

## Project Structure

- `src/` contains the action source code (see `CLAUDE.md` for a module-by-module
  breakdown)
- `tests/` contains pytest tests
- `demo/` contains the interactive demo site
- `.code-to-docs/` contains per-repo configuration (style guidelines, ignore
  lists, settings)

## Reporting Issues

Use the provided issue templates for bug reports and feature requests. Include
the model backend, model name, docs format, and trigger command when reporting
bugs.
