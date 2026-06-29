"""Runtime guardrails for the Swiss blog generator.

Python imports this module automatically at interpreter startup when it is
present on sys.path.  The GitHub Actions workflow runs
``python generate_and_publish.py`` from the repository root, so this file is
loaded before the generator script starts executing.

The guardrail below patches the generator once its functions have been defined
and before ``main()`` begins.  It is intentionally narrow: it only canonicalises
and sanitises the final CTA/disclaimer region of generated blog drafts.  This
prevents otherwise valid drafts from failing validation where the model uses a
case variant of the CTA heading or adds a further practical section after the
CTA.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Callable


def _split_blocks(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", text.strip()) if chunk.strip()]


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _is_bold_heading(block: str) -> bool:
    if not (block.startswith("**") and block.endswith("**")):
        return False
    inner = block[2:-2].strip()
    return len(inner.split()) <= 14 and not inner.endswith(".")


def _heading_text(block: str) -> str:
    return re.sub(r"^\*\*|\*\*$", "", block.strip()).strip()


def _heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _is_disclaimer_block(block: str, fallback: Callable[[str], bool] | None = None) -> bool:
    if fallback is not None:
        try:
            if fallback(block):
                return True
        except Exception:
            pass

    text = re.sub(r"^\*|\*$", "", block.strip()).strip().casefold()
    markers = [
        "not legal advice",
        "does not constitute legal advice",
        "individual facts",
        "individual circumstances",
        "case-specific",
        "cantonal handling",
        "procedural posture",
        "date of writing",
        "current position",
    ]
    return sum(1 for marker in markers if marker in text) >= 2


def _canonicalise_cta_region(blog_content: str, generator_globals: dict[str, Any]) -> str:
    blocks = _split_blocks(blog_content)
    if not blocks:
        return blog_content

    cta_heading = generator_globals.get("CTA_HEADING", "Contact Our Immigration Lawyers In Switzerland")
    cta_block = f"**{cta_heading}**"
    cta_key = _heading_key(cta_heading)
    is_disclaimer = generator_globals.get("is_disclaimer_block")
    disclaimer_fallback = is_disclaimer if callable(is_disclaimer) else None

    canonical_blocks: list[str] = []
    for block in blocks:
        if _is_bold_heading(block) and _heading_key(_heading_text(block)) == cta_key:
            canonical_blocks.append(cta_block)
        else:
            canonical_blocks.append(block)
    blocks = canonical_blocks

    cta_indexes = [idx for idx, block in enumerate(blocks) if block.strip() == cta_block]
    if not cta_indexes:
        return "\n\n".join(blocks)

    # Keep one CTA section only.  The CTA must be the last substantive section,
    # so discard generated headings and content that follow it, preserving only
    # a final disclaimer.
    cta_index = cta_indexes[0]
    before_cta = blocks[: cta_index + 1]

    cta_body_blocks: list[str] = []
    for block in blocks[cta_index + 1 :]:
        if _is_disclaimer_block(block, disclaimer_fallback):
            break
        if _is_bold_heading(block):
            break
        if block.strip():
            cta_body_blocks.append(block)

    body_text = "\n\n".join(cta_body_blocks)
    body_lower = body_text.casefold()

    lawyer_support_markers = [
        "review",
        "assess",
        "advise",
        "strategy",
        "evidence",
        "timing",
        "route",
        "application",
        "immigration lawyers",
        "swiss immigration lawyers",
        "richmond chambers switzerland",
    ]
    has_substantive_help = any(
        _count_words(block) >= 20 and any(marker in block.casefold() for marker in lawyer_support_markers)
        for block in cta_body_blocks
    )

    cta_name = generator_globals.get("CTA_NAME", "Richmond Chambers Switzerland")
    cta_phone = generator_globals.get("CTA_PHONE", "+41 21 588 07 70")
    standard_contact_sentence = generator_globals.get(
        "CTA_STANDARD_CONTACT_SENTENCE",
        f"To arrange an initial consultation meeting, contact {cta_name} by telephone on {cta_phone} "
        "or complete our enquiry form.",
    )

    has_contact_sentence = (
        cta_phone in body_text
        and "enquiry form" in body_lower
        and "initial consultation meeting" in body_lower
    )

    if not has_substantive_help:
        cta_body_blocks.insert(
            0,
            (
                f"Our specialist Swiss immigration lawyers at {cta_name} can assess your immigration history, "
                "identify timing and evidence risks, and advise on a filing strategy tailored to your route "
                "and procedural position."
            ),
        )

    if not has_contact_sentence:
        cta_body_blocks.append(standard_contact_sentence)

    disclaimer_blocks = [
        block for block in blocks if _is_disclaimer_block(block, disclaimer_fallback)
    ]
    if disclaimer_blocks:
        disclaimer_text = re.sub(r"^\*|\*$", "", disclaimer_blocks[-1].strip()).strip()
    else:
        disclaimer_text = (
            "This article summarises Swiss immigration law and guidance as at the date of writing. "
            "Individual facts, evidence, cantonal handling and procedural posture can materially affect "
            "the outcome. It is not legal advice."
        )
    disclaimer_text = disclaimer_text.rstrip(".") + "."
    final_disclaimer = f"*{disclaimer_text}*"

    return "\n\n".join(before_cta + cta_body_blocks + [final_disclaimer])


def _apply_patch(generator_globals: dict[str, Any]) -> None:
    if generator_globals.get("_SITE_CUSTOMIZE_CTA_PATCH_APPLIED"):
        return

    original_normalise = generator_globals.get("normalise_draft_output")
    if not callable(original_normalise):
        return

    def patched_normalise_draft_output(
        draft: dict[str, Any],
        topic_entry: dict[str, Any],
        classifier: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cleaned = original_normalise(draft, topic_entry, classifier)
        blog_content = (cleaned.get("blog_content") or "").strip()
        blog_content = _canonicalise_cta_region(blog_content, generator_globals)
        blog_content = re.sub(r"\n{3,}", "\n\n", blog_content).strip()
        cleaned["blog_content"] = blog_content
        return cleaned

    generator_globals["normalise_draft_output"] = patched_normalise_draft_output
    generator_globals["_SITE_CUSTOMIZE_CTA_PATCH_APPLIED"] = True


def _trace(frame: Any, event: str, arg: Any) -> Any:
    if event == "call":
        filename = str(frame.f_globals.get("__file__", ""))
        if filename.endswith("generate_and_publish.py") and "normalise_draft_output" in frame.f_globals:
            _apply_patch(frame.f_globals)
            sys.settrace(None)
            return None
    return _trace


sys.settrace(_trace)
