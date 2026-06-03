"""Anki flashcard models."""

from typing import List
from pydantic import BaseModel, Field


class AnkiCard(BaseModel):
    """A flashcard. Either a basic Q/A card or a cloze (fill-in-the-blank) card."""
    type: str = Field(default="basic", description="'basic' for a question/answer card, or 'cloze' for a fill-in-the-blank card")
    question: str = Field(default="", description="Basic cards: the front - a clear, self-contained question")
    answer: str = Field(default="", description="Basic cards: the back - a concise, correct answer")
    text: str = Field(default="", description="Cloze cards: a full sentence with the key fact hidden using {{c1::...}} (and {{c2::...}} for more)")
    tags: List[str] = Field(default_factory=list, description="Optional topic tags, lowercase, no spaces")


class AnkiCardSet(BaseModel):
    """A set of flashcards extracted from a piece of content."""
    cards: List[AnkiCard] = Field(description="One or more flashcards capturing the key facts in the content")
