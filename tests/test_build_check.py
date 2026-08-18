"""Tests for build_check.py -- docs build verification and code sample checking."""

from build_check import check_code_samples, run_docs_build


class TestRunDocsBuild:
    def test_no_command_succeeds(self, tmp_path):
        ok, err = run_docs_build("", str(tmp_path))
        assert ok is True
        assert err == ""

    def test_successful_build(self, tmp_path):
        ok, err = run_docs_build("true", str(tmp_path))
        assert ok is True

    def test_failed_build(self, tmp_path):
        ok, err = run_docs_build("echo 'broken directive' && exit 1", str(tmp_path))
        assert ok is False
        assert "broken directive" in err

    def test_timeout(self, tmp_path):
        ok, err = run_docs_build("sleep 10", str(tmp_path), timeout=1)
        assert ok is False
        assert "timed out" in err.lower()


class TestCheckCodeSamples:
    def test_valid_python(self):
        content = '# Guide\n\n```python\nprint("hello")\n```\n'
        assert check_code_samples(content) == []

    def test_invalid_python(self):
        content = "# Guide\n\n```python\ndef broken(\n```\n"
        issues = check_code_samples(content)
        assert len(issues) == 1
        assert issues[0][1] == "python"

    def test_valid_json(self):
        content = '# Config\n\n```json\n{"key": "value"}\n```\n'
        assert check_code_samples(content) == []

    def test_invalid_json(self):
        content = "# Config\n\n```json\n{broken}\n```\n"
        issues = check_code_samples(content)
        assert len(issues) == 1
        assert issues[0][1] == "json"

    def test_unknown_language_skipped(self):
        content = '```ruby\nputs "hello"\n```\n'
        assert check_code_samples(content) == []

    def test_multiple_blocks(self):
        content = '```python\nx = 1\n```\n\n```json\n{"a": 1}\n```\n'
        assert check_code_samples(content) == []

    def test_line_number_tracking(self):
        content = "Line 1\nLine 2\nLine 3\n\n```python\ndef broken(\n```\n"
        issues = check_code_samples(content)
        assert issues[0][0] == 5
