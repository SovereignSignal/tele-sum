"""Generic 'digest' extraction profile: one Telegram post -> many structured items.

Channel-agnostic on purpose (open-source composability): repo digests, model
release notes, news roundups, jobs lists all fit. Per-channel flavor comes from
channel_configs.prompt_hint, not from per-channel schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from src.schemas.entities import ConfidenceStr


class DigestItem(BaseModel):
    title: str = Field(min_length=1)
    url: Optional[str] = None
    note: str = ""                      # what it is / why it matters, 1-2 lines
    category: Optional[str] = None      # source's own section label (e.g. release/security)
    tags: List[str] = Field(default_factory=list)
    org: Optional[str] = None

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


class DigestAnalysis(BaseModel):
    """Validated AI response for the digest profile."""
    title: str = ""
    summary: str = ""
    items: List[DigestItem] = Field(default_factory=list)
    confidence: ConfidenceStr = "confident"
