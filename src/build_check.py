"""Pre-commit verification for generated documentation.

Runs an optional docs build command and syntax-checks fenced code samples
before the generated content is committed or pushed.
"""

import json
import re
import shutil
import subprocess
import tempfile

from security_utils import sanitize_output


def run_docs_build(build_command, docs_root, timeout=120):
    """Run a docs build command in a temp copy of the docs tree.

    Returns (success, error_output).
    """
    if not build_command:
        return True, ""

    tmpdir = tempfile.mkdtemp(prefix="code-to-docs-build-")
    try:
        shutil.copytree(docs_root, tmpdir, dirs_exist_ok=True)
        result = subprocess.run(
            build_command,
            shell=True,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "Build failed").strip()
            return False, sanitize_output(error[:2000])
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"Build timed out after {timeout}s"
    except Exception as e:
        return False, sanitize_output(str(e))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


_FENCE_PATTERN = re.compile(
    r"^```(\w+)\s*\n(.*?)^```\s*$",
    re.MULTILINE | re.DOTALL,
)


def check_code_samples(content, file_path=""):
    """Syntax-check fenced code blocks in generated documentation.

    Returns a list of (line_number, language, error) tuples.
    Does not execute any code.
    """
    issues = []
    for match in _FENCE_PATTERN.finditer(content):
        lang = match.group(1).lower()
        code = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        if lang == "python":
            try:
                compile(code, f"{file_path}:line{line_num}", "exec")
            except SyntaxError as e:
                issues.append((line_num, lang, str(e)))

        elif lang == "json":
            try:
                json.loads(code)
            except json.JSONDecodeError as e:
                issues.append((line_num, lang, str(e)))

        elif lang == "yaml":
            try:
                import yaml

                yaml.safe_load(code)
            except ImportError:
                pass
            except yaml.YAMLError as e:
                issues.append((line_num, lang, str(e)))

    return issues
