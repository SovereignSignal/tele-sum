"""Bound source excerpts without discarding late application sections."""

import re

MAX_SOURCE_CHARS = 6000
_RELEVANT = re.compile(
    r"\b(?:deadline|eligib\w*|apply|applicants?|applications?|submissions?|"
    r"timetable|schedule|duration|budget|funding|register|registration)\b", re.I,
)


def source_excerpt(text, max_chars=MAX_SOURCE_CHARS):
    if len(text) <= max_chars:
        return text
    # Keep the introduction and complete blocks around late application facts.
    # Explicit gap markers prevent separated sections from reading as one item.
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) == 1:
        return text[:max_chars - 4] + " ..."
    selected = set()
    used = 0
    for i, block in enumerate(blocks):
        if used + len(block) + 2 > max_chars // 2:
            break
        selected.add(i)
        used += len(block) + 2
    # Prioritize explicit application terms, retaining adjacent table rows/dates.
    ranked = sorted(
        (i for i, block in enumerate(blocks) if _RELEVANT.search(block)),
        key=lambda i: (-bool(re.search(r"\b(?:deadline|timetable|eligib\w*|apply|submission\w*)\b", blocks[i], re.I)), i),
    )
    for i in ranked:
        group = set(range(max(0, i - 1), min(len(blocks), i + 2))) - selected
        cost = sum(len(blocks[j]) + 2 for j in group) + 30
        if used + cost <= max_chars:
            selected.update(group)
            used += cost
    parts = []
    previous = -1
    for i in sorted(selected):
        if i != previous + 1:
            parts.append("[Source section omitted]")
        parts.append(blocks[i])
        previous = i
    if previous != len(blocks) - 1:
        parts.append("[Source section omitted]")
    if not selected:
        return text[:max_chars - 4] + " ..."
    return "\n\n".join(parts)[:max_chars]
