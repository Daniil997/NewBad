"""Keep evidence IDs in structured citations rather than user-facing prose."""

import re
from collections.abc import Iterable


def clean_source_references(text: str, evidence_ids: Iterable[str]) -> str:
    ids = sorted({value for value in evidence_ids if isinstance(value, str) and value}, key=len, reverse=True)
    if not ids:
        return text
    token = "(?:" + "|".join(re.escape(value) for value in ids) + ")"
    group = rf"{token}(?:\s*[,;]\s*{token})*"
    # Preserve the human label when a model invents a Markdown link to an internal ID.
    cleaned = re.sub(rf"\[([^\]\n]+)\]\(\s*{token}\s*\)", r"\1", text)
    cleaned = re.sub(rf"\(\s*{group}\s*\)|\[\s*{group}\s*\]|`\s*{group}\s*`", "", cleaned)
    cleaned = re.sub(rf"(?<![\w:/.-]){token}(?![\w:/-]|\.\w)", "", cleaned)
    if cleaned == text:
        return text
    cleaned = re.sub(r"(\S)[ \t]{2,}", r"\1 ", cleaned)
    cleaned = re.sub(r"[ \t]+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\.)\.\.(?!\.)", ".", cleaned)
    return cleaned.strip()


def display_report(report: dict) -> dict:
    """Return a display copy, including for old cached reports; preserve source snapshots."""
    ids = [value for value in report.get("evidence_ids", []) if isinstance(value, str)]
    ids.extend(source["evidence_id"] for source in report.get("bank_sources", [])
               if isinstance(source, dict) and isinstance(source.get("evidence_id"), str))
    result = dict(report)
    for field in ("summary", "relevance", "risks", "opportunities", "next_step", "impact_channel"):
        if isinstance(result.get(field), str):
            result[field] = clean_source_references(result[field], ids)
    if isinstance(result.get("questions"), list):
        result["questions"] = [clean_source_references(item, ids) if isinstance(item, str) else item
                               for item in result["questions"]]
    return result
