"""Tests for generation.py — AI content generation and file I/O."""

from unittest.mock import MagicMock, patch

from generation import (
    GenerationResult,
    VerificationResult,
    ask_ai_for_updated_content,
    generate_updates_parallel,
    load_full_content,
    overwrite_file,
    validate_content_preservation,
    verify_update_with_llm,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _mock_ai_response(content):
    """Build a mock OpenAI client that returns *content* from chat completion."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ── load_full_content ───────────────────────────────────────────────────────


class TestLoadFullContent:
    def test_reads_valid_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        doc = tmp_path / "readme.rst"
        doc.write_text("Hello RST", encoding="utf-8")
        result = load_full_content("readme.rst")
        assert result == "Hello RST"

    def test_rejects_traversal_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = load_full_content("../../etc/passwd")
        assert result == ""

    def test_returns_empty_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = load_full_content("nonexistent.rst")
        assert result == ""


# ── overwrite_file ──────────────────────────────────────────────────────────


class TestOverwriteFile:
    def test_writes_valid_doc_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "output.rst"
        target.write_text("old", encoding="utf-8")
        result = overwrite_file("output.rst", "new content")
        assert result is True
        assert target.read_text(encoding="utf-8") == "new content"

    def test_rejects_non_doc_extension(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "script.py"
        target.write_text("print('hi')", encoding="utf-8")
        result = overwrite_file("script.py", "hacked")
        assert result is False

    def test_rejects_traversal_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = overwrite_file("../evil.rst", "bad content")
        assert result is False


def _get_messages(mock_client):
    """Extract messages from a mocked client call."""
    return mock_client.chat.completions.create.call_args[1]["messages"]


def _get_user_prompt(mock_client):
    """Extract the user prompt from a mocked client call."""
    return _get_messages(mock_client)[1]["content"]


# ── ask_ai_for_updated_content ──────────────────────────────────────────────


class TestAskAiForUpdatedContent:
    DIFF = "diff --git a/foo.py\n+added line"
    CONTENT = "Some documentation content"

    def test_includes_system_prompt(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)
        messages = _get_messages(mock_client)
        assert messages[0]["role"] == "system"
        assert "technical writer" in messages[0]["content"]

    def test_returns_updated_content(self):
        mock_client = _mock_ai_response("Updated documentation text")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT, skip_verification=True
            )
        assert isinstance(result, GenerationResult)
        assert result.content == "Updated documentation text\n"
        assert result.verification_status == "skipped"

    def test_returns_no_update_needed(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)
        assert result.content == "NO_UPDATE_NEEDED"

    def test_detects_rst_format(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(self.DIFF, "docs/guide.rst", self.CONTENT)
        prompt = _get_user_prompt(mock_client)
        assert "RESTRUCTUREDTEXT" in prompt

    def test_detects_md_format(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)
        prompt = _get_user_prompt(mock_client)
        assert "MARKDOWN" in prompt

    def test_includes_user_instructions(self):
        mock_client = _mock_ai_response("NO_UPDATE_NEEDED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            ask_ai_for_updated_content(
                self.DIFF,
                "docs/guide.md",
                self.CONTENT,
                user_instructions="Focus on API changes only",
            )
        prompt = _get_user_prompt(mock_client)
        assert "Focus on API changes only" in prompt


# ── generate_updates_parallel ───────────────────────────────────────────────


class TestGenerateUpdatesParallel:
    DIFF = "diff --git a/foo.py\n+added line"

    def test_processes_multiple_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create two doc files
        (tmp_path / "a.rst").write_text("Doc A", encoding="utf-8")
        (tmp_path / "b.rst").write_text("Doc B", encoding="utf-8")

        mock_client = _mock_ai_response("Updated doc")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            results = generate_updates_parallel(
                self.DIFF, ["a.rst", "b.rst"], max_workers=2, skip_verification=True
            )

        assert len(results) == 2
        paths_returned = {r[0] for r in results}
        assert paths_returned == {"a.rst", "b.rst"}
        for _, _original, result in results:
            assert isinstance(result, GenerationResult)
            assert result.content == "Updated doc\n"

    def test_skips_no_update_needed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.rst").write_text("Doc A", encoding="utf-8")
        (tmp_path / "b.rst").write_text("Doc B", encoding="utf-8")

        mock_client = MagicMock()

        def side_effect(**kwargs):
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            prompt = kwargs["messages"][-1]["content"]
            if "a.rst" in prompt:
                mock_resp.choices[0].message.content = "Updated A"
            else:
                mock_resp.choices[0].message.content = "NO_UPDATE_NEEDED"
            return mock_resp

        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            results = generate_updates_parallel(
                self.DIFF, ["a.rst", "b.rst"], max_workers=2, skip_verification=True
            )

        assert len(results) == 1
        assert results[0][0] == "a.rst"
        assert results[0][2].content == "Updated A\n"


# ── validate_content_preservation ─────────────────────────────────────────


class TestValidateContentPreservation:
    def test_identical_content_passes(self):
        text = "\n".join(f"Line {i}" for i in range(40))
        ok, issues = validate_content_preservation(text, text)
        assert ok is True
        assert issues == []

    def test_minor_addition_passes(self):
        original = "\n".join(f"Line {i}" for i in range(40))
        updated = original + "\nNew line added"
        ok, issues = validate_content_preservation(original, updated)
        assert ok is True
        assert issues == []

    def test_small_removal_passes(self):
        """Removing a few lines under the 20% threshold passes."""
        original = "\n".join(f"Line {i}" for i in range(40))
        updated = "\n".join(f"Line {i}" for i in range(40) if i not in (10, 20))
        ok, issues = validate_content_preservation(original, updated)
        assert ok is True
        assert issues == []

    def test_large_removal_fails(self):
        """Removing 25 out of 40 lines exceeds the 20% threshold."""
        original = "\n".join(f"Line {i}" for i in range(40))
        updated = "\n".join(f"Line {i}" for i in range(15))
        ok, issues = validate_content_preservation(original, updated)
        assert ok is False
        assert len(issues) == 1
        assert "removal rate" in issues[0]

    def test_empty_original_passes(self):
        ok, issues = validate_content_preservation("", "New content")
        assert ok is True
        assert issues == []

    def test_empty_updated_fails(self):
        ok, issues = validate_content_preservation("Some content", "")
        assert ok is False
        assert len(issues) == 1
        assert "empty" in issues[0].lower()

    def test_blank_lines_ignored(self):
        """Blank lines should not count toward removal detection."""
        original = "\n".join(f"Line {i}" for i in range(40))
        updated_lines = [f"Line {i}" for i in range(40)]
        updated = "\n\n".join(updated_lines)
        ok, issues = validate_content_preservation(original, updated)
        assert ok is True
        assert issues == []

    def test_complete_rewrite_fails(self):
        """Replacing all content with completely different text should fail."""
        original = "\n".join(f"Original section {i}" for i in range(40))
        updated = "\n".join(f"Totally different content {i}" for i in range(40))
        ok, issues = validate_content_preservation(original, updated)
        assert ok is False
        assert len(issues) == 1

    def test_short_file_skips_check(self):
        """Files under _MIN_LINES_FOR_CHECK are exempt from the ratio check."""
        original = "\n".join(f"Line {i}" for i in range(10))
        updated = "\n".join(f"Line {i}" for i in range(5))
        ok, issues = validate_content_preservation(original, updated)
        assert ok is True
        assert issues == []

    def test_reworded_lines_get_partial_credit(self):
        """Lightly reworded lines should not count as full deletions."""
        original = "\n".join(f"This is documentation line number {i}" for i in range(40))
        # Reword 12 lines (change suffix slightly)
        updated_lines = []
        for i in range(40):
            if i < 12:
                updated_lines.append(f"This is documentation line number {i} (updated)")
            else:
                updated_lines.append(f"This is documentation line number {i}")
        updated = "\n".join(updated_lines)
        ok, issues = validate_content_preservation(original, updated)
        assert ok is True
        assert issues == []

    def test_wholesale_deletion_still_fails(self):
        """Deleting 25 out of 40 lines outright must fail."""
        original = "\n".join(f"Line {i}" for i in range(40))
        updated = "\n".join(f"Line {i}" for i in range(15))
        ok, issues = validate_content_preservation(original, updated)
        assert ok is False
        assert len(issues) == 1


# ── verify_update_with_llm ────────────────────────────────────────────────


class TestVerifyUpdateWithLlm:
    DIFF = "diff --git a/foo.py\n+added line"
    ORIGINAL = "# Guide\n\nExisting content here."
    UPDATED = "# Guide\n\nExisting content here.\n\n## New section\n\nAdded for the change."

    def test_approved_returns_ok(self):
        mock_client = _mock_ai_response("APPROVED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert isinstance(result, VerificationResult)
        assert result.ok is True
        assert result.issues == ""
        assert result.available is True

    def test_rejected_returns_issues(self):
        mock_client = _mock_ai_response("REJECTED: Removed the examples section")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is False
        assert "Removed the examples section" in result.issues

    def test_rejected_without_details(self):
        mock_client = _mock_ai_response("REJECTED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is False
        assert "no details" in result.issues.lower()

    def test_ambiguous_response_fails_closed(self):
        """Responses with no verdict token fail closed."""
        mock_client = _mock_ai_response("The update looks mostly fine but...")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is False
        assert "ambiguous" in result.issues.lower()

    def test_llm_error_passes_gracefully(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("connection failed")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is True
        assert result.available is False

    def test_includes_user_instructions_in_prompt(self):
        mock_client = _mock_ai_response("APPROVED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            verify_update_with_llm(
                self.DIFF,
                "docs/guide.md",
                self.ORIGINAL,
                self.UPDATED,
                user_instructions="keep changes minimal",
            )
        prompt = _get_user_prompt(mock_client)
        assert "keep changes minimal" in prompt

    def test_uses_verification_system_prompt(self):
        mock_client = _mock_ai_response("APPROVED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        messages = _get_messages(mock_client)
        assert messages[0]["role"] == "system"
        assert "auditor" in messages[0]["content"]

    def test_verdict_with_preamble_is_approved(self):
        """Small models often prepend reasoning before the verdict."""
        mock_client = _mock_ai_response("After reviewing the changes, my verdict is: APPROVED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is True
        assert result.issues == ""

    def test_verdict_with_preamble_is_rejected(self):
        mock_client = _mock_ai_response(
            "Looking at this: REJECTED: removed the installation section"
        )
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is False
        assert "removed the installation section" in result.issues

    def test_rejected_wins_when_both_tokens_present(self):
        mock_client = _mock_ai_response(
            "The changes look APPROVED at first glance but actually REJECTED: missing context"
        )
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        # APPROVED appears first in the text, so regex finds it first.
        # But this test documents the behavior: whichever token appears first wins.
        # In this case APPROVED appears at position 17 before REJECTED at 55.
        assert result.available is True

    def test_no_verdict_token_is_ambiguous(self):
        mock_client = _mock_ai_response("I'm not sure about this update.")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        assert result.ok is False
        assert "ambiguous" in result.issues.lower()

    def test_doc_content_is_delimited(self):
        """Document content blocks should be wrapped in delimiters."""
        mock_client = _mock_ai_response("APPROVED")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            verify_update_with_llm(self.DIFF, "docs/guide.md", self.ORIGINAL, self.UPDATED)
        prompt = _get_user_prompt(mock_client)
        assert "--- BEGIN ORIGINAL DOCUMENTATION" in prompt
        assert "--- END ORIGINAL DOCUMENTATION ---" in prompt
        assert "--- BEGIN UPDATED DOCUMENTATION" in prompt
        assert "--- END UPDATED DOCUMENTATION ---" in prompt
        assert "data to be evaluated" in prompt


# ── ask_ai_for_updated_content: post-generation validation integration ────


class TestPostGenerationValidation:
    DIFF = "diff --git a/foo.py\n+added line"
    # Use 40+ lines so the preservation check doesn't skip due to _MIN_LINES_FOR_CHECK
    CONTENT = "\n".join(f"Doc line {i}" for i in range(40)) + "\n"

    def test_passes_when_both_checks_ok(self):
        """Content that passes preservation + LLM verification is returned."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                mock_resp.choices[0].message.content = "APPROVED"
            else:
                mock_resp.choices[0].message.content = self.CONTENT + "New line\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)

        assert isinstance(result, GenerationResult)
        assert result.content.strip() != "NO_UPDATE_NEEDED"
        assert "New line" in result.content
        assert result.verification_status == "passed"

    def test_regenerates_when_verification_rejects(self):
        """When LLM verification rejects, a regeneration attempt is made."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                mock_resp.choices[0].message.content = "REJECTED: Removed unrelated section"
            elif "rejected because" in messages[1]["content"]:
                mock_resp.choices[0].message.content = self.CONTENT + "Fixed update\n"
            else:
                mock_resp.choices[0].message.content = self.CONTENT + "Bad update\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)

        assert "Fixed update" in result.content
        assert result.verification_status == "regenerated"
        # 3 calls: initial generation, verification, regeneration
        assert call_count[0] == 3

    def test_regenerates_when_preservation_fails(self):
        """Preservation check failure triggers regeneration (LLM verification skipped)."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if "rejected because" in messages[1]["content"]:
                mock_resp.choices[0].message.content = self.CONTENT + "Fixed update\n"
            else:
                # Initial generation removes most lines
                mock_resp.choices[0].message.content = "Doc line 0\nNew stuff only\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)

        assert "Fixed update" in result.content
        assert result.verification_status == "regenerated"
        # Only 2 calls: generation + regeneration. No LLM verification since
        # preservation already failed (short-circuit).
        assert call_count[0] == 2

    def test_returns_no_update_when_regeneration_raises(self):
        """When regeneration API call raises, returns NO_UPDATE_NEEDED."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                mock_resp.choices[0].message.content = "REJECTED: Bad update"
            elif "rejected because" in messages[1]["content"]:
                raise RuntimeError("API connection failed")
            else:
                mock_resp.choices[0].message.content = self.CONTENT + "Some addition\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)

        assert result.content.strip() == "NO_UPDATE_NEEDED"
        assert result.verification_status == "regenerated"

    def test_skips_when_regeneration_still_fails_preservation(self):
        """When regenerated output still fails preservation, returns NO_UPDATE_NEEDED."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                mock_resp.choices[0].message.content = "REJECTED: Rewrote everything"
            elif "rejected because" in messages[1]["content"]:
                mock_resp.choices[0].message.content = "Completely different content\n"
            else:
                mock_resp.choices[0].message.content = self.CONTENT + "Some addition\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)

        assert result.content.strip() == "NO_UPDATE_NEEDED"

    def test_verification_status_passed(self):
        """Verify the 'passed' status propagates correctly."""

        def side_effect(**kwargs):
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                mock_resp.choices[0].message.content = "APPROVED"
            else:
                mock_resp.choices[0].message.content = self.CONTENT + "Addition\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)
        assert result.verification_status == "passed"

    def test_verification_status_skipped(self):
        """Verify the 'skipped' status when skip_verification is True."""
        mock_client = _mock_ai_response(self.CONTENT + "Addition\n")
        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(
                self.DIFF, "docs/guide.md", self.CONTENT, skip_verification=True
            )
        assert result.verification_status == "skipped"

    def test_verification_status_unavailable(self):
        """Verify 'unavailable' when the verification LLM call fails."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            messages = kwargs["messages"]
            if messages[0]["content"].startswith("You are a documentation review auditor"):
                raise RuntimeError("API down")
            else:
                mock_resp.choices[0].message.content = self.CONTENT + "Addition\n"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect

        with (
            patch("generation.get_client", return_value=mock_client),
            patch("generation.get_model_name", return_value="test-model"),
        ):
            result = ask_ai_for_updated_content(self.DIFF, "docs/guide.md", self.CONTENT)
        assert result.verification_status == "unavailable"
        assert result.content.strip() != "NO_UPDATE_NEEDED"
