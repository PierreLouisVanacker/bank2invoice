"""Extraction de l'émetteur depuis un libellé bancaire.

Pipeline :
  1. Préprocessing regex : isoler la zone "émetteur" du libellé brut, retirer
     les références bancaires, les motifs, les codes alphanumériques longs.
  2. Appel LLM sur la zone nettoyée.
  3. Post-traitement : normaliser civilités, etc.

L'étape 1 réduit le bruit envoyé au LLM, ce qui (a) accélère les appels,
(b) améliore la qualité, (c) limite la fenêtre où le LLM peut halluciner.
"""

from __future__ import annotations

import re

from app.llm.base import EmetteurExtrait, LLMClient

# Patterns de bruit à supprimer du libellé avant envoi au LLM
_NOISE_PATTERNS = [
    # Hash hexadécimal long (20+ chars), même collé à d'autres caractères.
    # Ex: "168a4adbed4a11f0a439bf065156b2fcMlle" → on retire la partie hex,
    # le "Mlle" reste.
    re.compile(r"[a-f0-9]{20,}"),
    # Codes alphanumériques longs (refs SEPA, transactions, etc.) en mot isolé
    re.compile(r"\b[A-Z0-9]{16,}\b"),
    # "FR21ZZZ8200A9 Core" et autres SEPA mandate
    re.compile(r"\bFR\d{2}[A-Z0-9]{3,}\b"),
    # "Mandat", "Mandate", "Core", "Sepa"
    re.compile(r"\b(?:Mandate?|Core|SEPA)\b", re.IGNORECASE),
    # Date au format "Au JJ/MM/AA" ou similaires
    re.compile(r"\bAu\s+\d{1,2}/\d{1,2}/\d{2,4}\b", re.IGNORECASE),
]


def preprocess_libelle(libelle: str) -> str:
    """Nettoie le libellé pour limiter le bruit envoyé au LLM.

    Idempotent : appliquer plusieurs fois donne le même résultat.
    """
    cleaned = libelle

    # 1) On nettoie d'abord le bruit (hashes, refs SEPA, etc.). C'est important
    #    de le faire AVANT d'inspecter la 2e partie après " / " : les civilités
    #    sont parfois collées à un hash (cas "168a4adbed...Mlle le duc Noemie").
    for pattern in _NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    # 2) On ne garde que la première partie avant " / " (ce qui suit est en
    #    général une référence/motif), SAUF si la 2e partie contient une
    #    civilité — auquel cas elle contient potentiellement le vrai nom.
    if " / " in cleaned:
        first, rest = cleaned.split(" / ", 1)
        if re.search(r"\b(M\.?|Mme|Mlle|Mr)\b", rest, re.IGNORECASE):
            cleaned = f"{first} / {rest}"
        else:
            cleaned = first

    # 3) Compactage des espaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_emetteur(libelle: str, llm: LLMClient) -> EmetteurExtrait:
    """Extrait l'émetteur d'un libellé bancaire via préprocessing + LLM."""
    if not libelle or not libelle.strip():
        return EmetteurExtrait(type="inconnu")

    cleaned = preprocess_libelle(libelle)
    if not cleaned:
        return EmetteurExtrait(type="inconnu")

    result = llm.extract_emetteur(cleaned)
    return _post_process(result)


def _post_process(em: EmetteurExtrait) -> EmetteurExtrait:
    """Normalise les civilités et nettoie les champs."""
    civilite_map = {
        "m": "M.", "mr": "M.", "m.": "M.", "monsieur": "M.",
        "mme": "Mme", "madame": "Mme",
        "mlle": "Mlle", "mademoiselle": "Mlle",
    }
    civilite = em.civilite
    if civilite:
        normalized = civilite_map.get(civilite.lower().rstrip("."), civilite)
        civilite = normalized

    # Si type='personne' mais nom ou prenom vides → on dégrade en inconnu
    if em.type == "personne" and not (em.nom or em.prenom):
        return EmetteurExtrait(type="inconnu")
    # Si type='societe' mais raison_sociale vide → idem
    if em.type == "societe" and not em.raison_sociale:
        return EmetteurExtrait(type="inconnu")

    return EmetteurExtrait(
        type=em.type,
        civilite=civilite,
        prenom=em.prenom,
        nom=em.nom,
        raison_sociale=em.raison_sociale,
        confidence=em.confidence,
    )
