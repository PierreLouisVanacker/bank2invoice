"""Abstraction LLM : Protocol pour pouvoir switcher Ollama / Anthropic API.

Le pipeline appelle uniquement `LLMClient.extract_emetteur(...)`. Les
implémentations concrètes vivent dans `ollama.py`, `anthropic.py`, etc.

Sortie typée : `EmetteurExtrait`. Si le LLM retourne quelque chose d'invalide,
les implémentations doivent renvoyer un EmetteurExtrait avec type='inconnu'
plutôt que de lever une exception, pour ne pas casser le pipeline d'ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmetteurExtrait:
    """Résultat de l'extraction d'émetteur depuis un libellé bancaire."""

    type: str  # 'personne' | 'societe' | 'inconnu'
    nom: str | None = None
    prenom: str | None = None
    civilite: str | None = None  # 'M.' | 'Mme' | 'Mlle' | None
    raison_sociale: str | None = None
    confidence: float = 0.0  # 0.0 à 1.0, informatif


class LLMClient(Protocol):
    """Interface minimale qu'un client LLM doit implémenter."""

    def extract_emetteur(self, libelle: str) -> EmetteurExtrait:
        """Extrait nom/prénom/société depuis un libellé bancaire."""
        ...


# Le JSON schema utilisé par les implémentations pour contraindre la sortie LLM.
# Compatible avec Ollama (option `format`) et Anthropic (tool_use).
EMETTEUR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["personne", "societe", "inconnu"],
            "description": "Personne physique, société/organisme, ou impossible à déterminer.",
        },
        "civilite": {
            "type": ["string", "null"],
            "description": "M., Mme, Mlle, Dr, etc. — null si absent.",
        },
        "prenom": {
            "type": ["string", "null"],
            "description": "Prénom(s) de la personne. null si type != 'personne'.",
        },
        "nom": {
            "type": ["string", "null"],
            "description": "Nom de famille. null si type != 'personne'.",
        },
        "raison_sociale": {
            "type": ["string", "null"],
            "description": "Nom de la société/organisme. null si type != 'societe'.",
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}
