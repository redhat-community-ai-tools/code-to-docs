"""Meta judges — guard against fixture/config errors that masquerade as passes.

The vacuous-pass hazard (DECISIONS findings, 2026-06-18): if a case's annotations fail to
load (e.g. a missing `dataset:` block, a typo'd annotations.yaml), `record["annotations"]`
defaults to `{}`. The selection judge then misscores every positive case as a false
"expected NONE" miss, and the content assertions iterate an empty rule set and pass having
checked NOTHING. Both look like results; neither is.

Every case must declare `expected_files` (even an empty list, for negatives). Its absence
means the answer key did not load. This judge fails loudly in that case, so a fixture error
shows up as a red judge instead of silent false confidence.
"""


def judge_annotations_present(outputs=None, **kwargs):
    """Fail unless the case's answer key actually loaded (has an `expected_files` key)."""
    ann = (outputs or {}).get("annotations")
    if not isinstance(ann, dict) or "expected_files" not in ann:
        return False, ("annotations missing or did not load (no 'expected_files' key) — "
                       "fixture/config error, not a real result")
    return True, "annotations loaded"
