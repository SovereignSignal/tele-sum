"""Generic 'digest' extraction profile: one Telegram post -> many structured items.

Channel-agnostic on purpose (open-source composability): repo digests, model
release notes, news roundups, jobs lists all fit. Per-channel flavor comes from
channel_configs.prompt_hint, not from per-channel schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from src.schemas.entities import ConfidenceStr


class Person(BaseModel):
    """A named individual tied to a digest item (author/founder/maintainer).

    Surfaced so a consumer can source contacts from `summaries.entities` directly
    instead of re-deriving them. `role`/`handle` are optional — the model emits
    name-only people when that is all the post gives.
    """
    name: str = Field(min_length=1)
    role: Optional[str] = None          # e.g. founder, maintainer, author
    handle: Optional[str] = None        # @x_handle or profile url, as the post gives it

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        # name is required (min_length=1); map None -> "" so a missing name fails
        # with a clean min_length error rather than a confusing string_type one.
        return str(v).strip() if v is not None else ""

    @field_validator("role", "handle", mode="before")
    @classmethod
    def blank_is_none(cls, v):
        v = (str(v).strip() if v is not None else "")
        return v or None


class DigestItem(BaseModel):
    title: str = Field(min_length=1)
    url: Optional[str] = None
    note: str = ""                      # what it is / why it matters, 1-2 lines
    category: Optional[str] = None      # source's own section label (e.g. release/security)
    tags: List[str] = Field(default_factory=list)
    org: Optional[str] = None
    people: List[Person] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v):
        return str(v).strip() if v is not None else v

    @field_validator("url", mode="before")
    @classmethod
    def blank_url_is_none(cls, v):
        v = (str(v).strip() if v is not None else "")
        return v or None

    @field_validator("note", mode="before")
    @classmethod
    def note_never_none(cls, v):
        return "" if v is None else str(v)

    @field_validator("tags", mode="before")
    @classmethod
    def coerce_tags(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        return [str(v)]

    @field_validator("people", mode="before")
    @classmethod
    def coerce_people(cls, v):
        # tolerate a lone dict, a bare name string, and None; drop entries with no
        # usable name so a stray {} or {"role": ...} never becomes a phantom person.
        if v is None:
            return []
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            if isinstance(x, str):              # a bare name string
                x = {"name": x}
            if isinstance(x, dict) and str(x.get("name") or "").strip():
                out.append(x)
        return out


class DigestAnalysis(BaseModel):
    """Validated AI response for the digest profile."""
    title: str = ""
    summary: str = ""
    items: List[DigestItem] = Field(default_factory=list)
    confidence: ConfidenceStr = "confident"

    @field_validator("items", mode="before")
    @classmethod
    def coerce_items(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return [v]
        return []

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        v = str(v or "").lower().strip()
        return "confident" if v == "confident" else "uncertain"
