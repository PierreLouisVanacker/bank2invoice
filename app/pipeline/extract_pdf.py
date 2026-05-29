"""Extraction de texte et de mots positionnés depuis un PDF de relevé.

Cette couche est purement mécanique : elle lit le PDF avec pdfplumber et
retourne le texte brut + la liste des mots avec leurs coordonnées. La
structuration en transactions vit ailleurs (un module par banque).
"""

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class Word:
    """Un mot extrait avec sa position et le numéro de page."""

    text: str
    page: int  # 1-indexed
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class ExtractedPdf:
    """Résultat d'une extraction PDF."""

    text: str  # concaténation du texte de toutes les pages
    pages_text: list[str]  # texte page par page
    words: list[Word]  # mots positionnés (toutes pages)


def extract(pdf_path: str | Path) -> ExtractedPdf:
    """Extrait texte et mots positionnés depuis un PDF."""
    pages_text: list[str] = []
    words: list[Word] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            pages_text.append(page_text)

            for w in page.extract_words():
                words.append(
                    Word(
                        text=w["text"],
                        page=page_index,
                        x0=float(w["x0"]),
                        x1=float(w["x1"]),
                        top=float(w["top"]),
                        bottom=float(w["bottom"]),
                    )
                )

    return ExtractedPdf(
        text="\n".join(pages_text),
        pages_text=pages_text,
        words=words,
    )
