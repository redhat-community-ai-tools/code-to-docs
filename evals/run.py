"""Fixture-based evaluation harness for code-to-docs prompt quality.

Runs test cases against a real LLM endpoint and checks assertions
rather than comparing golden text. Requires MODEL_API_BASE, MODEL_API_KEY,
and MODEL_NAME environment variables.

Usage:
    uv run python evals/run.py [--case CASE_NAME] [--verbose]
"""

import argparse
import os
import sys
import time
from pathlib import Path

import yaml

# Add src/ to path so we can import the action's modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def discover_cases(fixtures_dir, filter_name=None):
    """Find all fixture cases in the fixtures directory."""
    cases = []
    for case_dir in sorted(Path(fixtures_dir).iterdir()):
        if not case_dir.is_dir():
            continue
        if filter_name and case_dir.name != filter_name:
            continue
        expectations_file = case_dir / "expectations.yaml"
        diff_file = case_dir / "diff.patch"
        if not expectations_file.exists() or not diff_file.exists():
            print(f"  Skipping {case_dir.name}: missing expectations.yaml or diff.patch")
            continue
        cases.append(case_dir)
    return cases


def load_case(case_dir):
    """Load a single test case from a fixture directory."""
    diff = (case_dir / "diff.patch").read_text(encoding="utf-8")
    expectations = yaml.safe_load((case_dir / "expectations.yaml").read_text(encoding="utf-8"))

    instructions_file = case_dir / "instructions.txt"
    instructions = ""
    if instructions_file.exists():
        instructions = instructions_file.read_text(encoding="utf-8").strip()

    before_dir = case_dir / "before"
    doc_files = {}
    if before_dir.exists():
        for doc in before_dir.rglob("*"):
            if doc.is_file() and doc.suffix in (".md", ".rst", ".adoc"):
                rel = str(doc.relative_to(before_dir))
                doc_files[rel] = doc.read_text(encoding="utf-8")

    return {
        "name": case_dir.name,
        "diff": diff,
        "instructions": instructions,
        "doc_files": doc_files,
        "expectations": expectations,
    }


def run_case(case, verbose=False):
    """Run a single test case and return (passed, failures, token_count)."""
    from generation import ask_ai_for_updated_content

    expectations = case["expectations"]
    failures = []
    total_tokens = 0

    selected = expectations.get("selected", [])
    not_selected = expectations.get("not_selected", [])
    content_checks = expectations.get("content_checks", {})
    expect_no_update = expectations.get("expect_no_update", False)

    results = {}
    for file_path, content in case["doc_files"].items():
        if verbose:
            print(f"    Generating update for {file_path}...")
        start = time.time()
        updated = ask_ai_for_updated_content(
            case["diff"],
            file_path,
            content,
            user_instructions=case["instructions"],
            skip_verification=True,
        )
        elapsed = time.time() - start
        if verbose:
            print(f"    {file_path}: {elapsed:.1f}s")

        is_no_update = updated.strip() == "NO_UPDATE_NEEDED"
        results[file_path] = {"updated": updated, "no_update": is_no_update}

    for f in selected:
        if f in results and results[f]["no_update"]:
            failures.append(f"Expected {f} to be updated, got NO_UPDATE_NEEDED")

    for f in not_selected:
        if f in results and not results[f]["no_update"]:
            failures.append(f"Expected {f} to return NO_UPDATE_NEEDED, but it was updated")

    if expect_no_update:
        for f, r in results.items():
            if not r["no_update"]:
                failures.append(f"Expected NO_UPDATE_NEEDED for {f}, but it was updated")

    for file_path, checks in content_checks.items():
        if file_path not in results or results[file_path]["no_update"]:
            failures.append(f"Cannot check content of {file_path}: not updated")
            continue
        content = results[file_path]["updated"]

        for phrase in checks.get("contains", []):
            if phrase not in content:
                failures.append(f"{file_path}: expected to contain '{phrase}'")

        for phrase in checks.get("not_contains", []):
            if phrase in content:
                failures.append(f"{file_path}: should not contain '{phrase}'")

        for heading in checks.get("heading_present", []):
            if heading not in content:
                failures.append(f"{file_path}: expected heading '{heading}' to be present")

    return len(failures) == 0, failures, total_tokens


def main():
    parser = argparse.ArgumentParser(description="Run code-to-docs eval fixtures")
    parser.add_argument("--case", help="Run only this case")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    for var in ("MODEL_API_BASE", "MODEL_NAME"):
        if not os.environ.get(var):
            print(f"Error: {var} environment variable is required")
            sys.exit(1)

    fixtures_dir = Path(__file__).parent / "fixtures"
    cases = discover_cases(fixtures_dir, args.case)

    if not cases:
        print("No fixture cases found.")
        sys.exit(1)

    print(f"Found {len(cases)} case(s)\n")

    results_table = []
    for case_dir in cases:
        case = load_case(case_dir)
        print(f"  Running: {case['name']}...")
        try:
            passed, failures, tokens = run_case(case, verbose=args.verbose)
            status = "PASS" if passed else "FAIL"
            results_table.append((case["name"], status, failures))
            if not passed and args.verbose:
                for f in failures:
                    print(f"    FAIL: {f}")
        except Exception as e:
            results_table.append((case["name"], "ERROR", [str(e)]))
            if args.verbose:
                print(f"    ERROR: {e}")

    print("\n" + "=" * 60)
    print(f"{'Case':<35} {'Result':<10}")
    print("-" * 60)
    for name, status, failures in results_table:
        print(f"{name:<35} {status:<10}")
        if status == "FAIL":
            for f in failures:
                print(f"  - {f}")
    print("=" * 60)

    passed = sum(1 for _, s, _ in results_table if s == "PASS")
    total = len(results_table)
    print(f"\n{passed}/{total} passed")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
