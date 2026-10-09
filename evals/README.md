# Evaluation Harness

Fixture-based evaluation for code-to-docs prompt quality. Tests run against
a real LLM endpoint and check assertions rather than comparing golden text.

## Running

```bash
# Set endpoint credentials
export MODEL_API_BASE="https://your-endpoint/v1"
export MODEL_API_KEY="your-key"
export MODEL_NAME="your-model"

# Run all cases
uv run python evals/run.py

# Run a single case
uv run python evals/run.py --case issue-52-deletion --verbose
```

## Adding a Case

Create a directory under `evals/fixtures/` with:

```
evals/fixtures/my-case/
  diff.patch          # The code diff to analyze
  before/             # Doc files in their original state
    docs/guide.md
    docs/api.md
  expectations.yaml   # Assertions to check
  instructions.txt    # (optional) User instructions
```

### expectations.yaml format

```yaml
# Files that should be updated (not return NO_UPDATE_NEEDED)
selected:
  - docs/guide.md

# Files that should NOT be updated
not_selected:
  - docs/unrelated.md

# Content checks on the generated output
content_checks:
  docs/guide.md:
    contains:
      - "new-flag"
    not_contains:
      - "deleted heading"
    heading_present:
      - "## Configuration"

# If true, all files should return NO_UPDATE_NEEDED
expect_no_update: false
```

Assertions are preferred over golden files because model output varies
across runs and model versions. Check for structural properties (headings
present, keywords included, sections preserved) rather than exact text.
