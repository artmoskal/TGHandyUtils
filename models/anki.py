"""Anki flashcard models."""

from typing import List
from pydantic import BaseModel, Field


class AnkiCard(BaseModel):
    """A single Anki flashcard (question/answer pair)."""
    question: str = Field(description="The front of the card - a clear, self-contained question")
    answer: str = Field(description="The back of the card - a concise, correct answer")
    tags: List[str] = Field(default_factory=list, description="Optional topic tags, lowercase, no spaces")


class AnkiCardSet(BaseModel):
    """A set of flashcards extracted from a piece of content."""
    cards: List[AnkiCard] = Field(description="One or more flashcards capturing the key facts in the content")
