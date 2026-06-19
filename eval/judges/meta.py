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
    """Fail unless the case's answer key loaded — guarding BOTH scored dimensions.

    Two vacuous-pass holes, both of which masquerade as real results:

    - **No `expected_files` key** — the answer key didn't load at all (e.g. a missing
      `dataset:` block in eval.yaml). The selection judge then misscores every positive as a
      false "expected NONE".

    - **No / empty `content` block** — `content_assertions` iterates zero rules and returns
      True without checking anything. Note this can't be caught by "content output exists ->
      require content key": in this harness, content is generated *only* for annotated files
      (D-20), so an omitted `content` block also means no content output — the omission would
      slip through. So we require a `content` block unconditionally. Every case in this corpus
      tests content (positives assert mentions, negatives assert `NO_UPDATE_NEEDED`), so an
      absent block is an authoring mistake, not a real pass.
    """
    ann = (outputs or {}).get("annotations")
    if not isinstance(ann, dict):
        return False, "annotations missing or did not load — fixture/config error"
    if "expected_files" not in ann:
        return False, "annotations has no 'expected_files' key — answer key did not load"
    if not ann.get("content"):
        return False, ("annotations has no/empty 'content' block — content_assertions would "
                       "pass vacuously; every case must declare per-file content rules")
    return True, "annotations loaded (expected_files + content present)"
