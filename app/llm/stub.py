"""LLM stub pour tests et fallback.

Comportement déterministe basé sur des règles simples. Ne couvre pas tous les
cas, mais permet :
  - de tester le pipeline sans Ollama qui tourne
  - de fonctionner en dégradé si Ollama est indisponible

Pour la vraie extraction, utiliser OllamaClient ou AnthropicClient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.llm.base import EmetteurExtrait

# Marqueurs typiques de société
_SOCIETE_MARKERS = ("sarl", "sas", "sasu", "sci", "eurl", "snc", "scp", "selarl")
# Civilités reconnues
_CIVILITES = {"m": "M.", "mr": "M.", "m.": "M.", "mme": "Mme", "mlle": "Mlle"}


@dataclass
class StubLLM:
    """Implémentation LLMClient sans réseau, basée sur des règles."""

    def extract_emetteur(self, libelle: str) -> EmetteurExtrait:
        # 1. Isoler la zone "émetteur" : tout ce qui suit "de " / "DE "
        m = re.search(r"\bde\s+(.+?)(?:\s*/|$)", libelle, re.IGNORECASE)
        if m:
            zone = m.group(1).strip()
        else:
            # Pas de "de" → on prend la partie après "Virement [type]"
            zone = re.sub(
                r"^(virement|vir(?:ement)?\s+inst|vir\s+sepa|vir\s+europ\w*)\s*",
                "",
                libelle,
                flags=re.IGNORECASE,
            ).strip()
            # Retirer un éventuel "/ref" en fin
            zone = re.sub(r"\s*/.*$", "", zone).strip()

        if not zone:
            return EmetteurExtrait(type="inconnu")

        # 2. Heuristique société : présence d'un marqueur juridique
        zone_lower = zone.lower()
        if any(m in zone_lower.split() for m in _SOCIETE_MARKERS):
            return EmetteurExtrait(
                type="societe",
                raison_sociale=zone,
                confidence=0.5,
            )

        # 3. Heuristique personne : civilité en tête + 2 tokens
        tokens = zone.split()
        if not tokens:
            return EmetteurExtrait(type="inconnu")

        civilite = None
        first = tokens[0].rstrip(".").lower()
        if first in _CIVILITES:
            civilite = _CIVILITES[first]
            tokens = tokens[1:]

        if len(tokens) >= 2:
            # Convention française : prénom Nom (souvent NOM en majuscules,
            # mais ici les libellés sont en title case donc on prend
            # arbitrairement le 1er = prénom, le reste = nom).
            prenom = tokens[0]
            nom = " ".join(tokens[1:])
            return EmetteurExtrait(
                type="personne",
                civilite=civilite,
                prenom=prenom,
                nom=nom,
                confidence=0.4,
            )

        # 1 seul token → on suppose société d'un mot
        return EmetteurExtrait(
            type="societe",
            raison_sociale=tokens[0],
            confidence=0.2,
        )
