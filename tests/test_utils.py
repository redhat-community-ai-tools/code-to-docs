"""Tests for utils module — retry_with_backoff decorator and calc_backoff_delay helper."""

from unittest.mock import patch, MagicMock

import pytest

from utils import retry_with_backoff, calc_backoff_delay


# ── calc_backoff_delay ─────────────────────────────────────────────────────


class TestCalcBackoffDelay:
    def test_default_multiplier(self):
        assert calc_backoff_delay(0) == 3
        assert calc_backoff_delay(1) == 6
        assert calc_backoff_delay(2) == 9

    def test_custom_multiplier(self):
        assert calc_backoff_delay(0, multiplier=2) == 2
        assert calc_backoff_delay(1, multiplier=2) == 4
        assert calc_backoff_delay(2, multiplier=2) == 6


# ── retry_with_backoff ────────────────────────────────────────────────────


class TestRetryWithBackoff:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry_with_backoff(max_retries=3)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert succeed() == "ok"
        assert call_count == 1

    @patch("utils.time.sleep")
    def test_retries_on_exception(self, mock_sleep):
        calls = []

        @retry_with_backoff(max_retries=3, delay_multiplier=2)
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("transient")
            return "recovered"

        assert flaky() == "recovered"
        assert len(calls) == 3
        # Sleep called twice (after attempt 0 and 1, not after final success)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2)   # attempt 0: (0+1)*2 = 2
        mock_sleep.assert_any_call(4)   # attempt 1: (1+1)*2 = 4

    @patch("utils.time.sleep")
    def test_returns_default_on_exhaustion(self, mock_sleep):
        @retry_with_backoff(max_retries=2, default=None)
        def always_fail():
            raise RuntimeError("permanent")

        assert always_fail() is None

    @patch("utils.time.sleep")
    def test_returns_custom_default(self, mock_sleep):
        @retry_with_backoff(max_retries=2, default=[])
        def always_fail():
            raise RuntimeError("permanent")

        assert always_fail() == []

    @patch("utils.time.sleep")
    def test_reraise_on_exhaustion(self, mock_sleep):
        @retry_with_backoff(max_retries=2, reraise=True)
        def always_fail():
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            always_fail()

    @patch("utils.time.sleep")
    def test_on_retry_callback(self, mock_sleep):
        callback = MagicMock()

        @retry_with_backoff(max_retries=3, delay_multiplier=3, on_retry=callback)
        def fail_twice():
            if callback.call_count < 2:
                raise RuntimeError("oops")
            return "done"

        fail_twice()
        assert callback.call_count == 2
        # First call: attempt=0, max_retries=3, exception, wait_time=3
        args = callback.call_args_list[0][0]
        assert args[0] == 0          # attempt
        assert args[1] == 3          # max_retries
        assert isinstance(args[2], RuntimeError)
        assert args[3] == 3          # wait_time: (0+1)*3

    @patch("utils.time.sleep")
    def test_delay_multiplier(self, mock_sleep):
        @retry_with_backoff(max_retries=4, delay_multiplier=5, default="gave_up")
        def always_fail():
            raise RuntimeError("nope")

        always_fail()
        # Sleep called 3 times (not after last attempt)
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(5)    # (0+1)*5
        mock_sleep.assert_any_call(10)   # (1+1)*5
        mock_sleep.assert_any_call(15)   # (2+1)*5
