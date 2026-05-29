"""Filtrage : décide si une transaction est un virement entrant à facturer.

Décision basée sur :
  1. Le sens (debit/credit) — on ne facture que des crédits
  2. Le préfixe du libellé (Virement, Vir Inst, etc.)
  3. Des règles d'exclusion (auto-virements, remboursements, admin)

Sortie : pour chaque transaction, un FilterDecision avec :
  - est_virement_entrant : bool
  - review_status : 'auto_ok' | 'a_valider' | 'exclu'
  - raison : libellé explicatif pour la UI / debug

Note : aucune transaction n'est marquée 'auto_ok' à ce stade. Le passage à
'auto_ok' arrive plus tard dans le pipeline (lot 2d, après matching client par
alias). Ici on se contente d'inclure/exclure et marquer 'a_valider'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ─── Préfixes d'inclusion : reconnaît un virement entrant ───────────────────
# Insensible à la casse, on cherche en préfixe (le libellé commence par)

_VIREMENT_PREFIXES = (
    "virement vir inst de",
    "virement vir inst ",  # parfois pas de "de"
    "virement web ",       # virement web par tiers
    "vir sepa",
    "vir europeen",
    "vir recu",
    "virement ",           # fallback générique en dernier
)

# ─── Patterns d'exclusion forte (le libellé matche → exclu d'office) ────────
# Insensible à la casse, on cherche en sous-chaîne.

_EXCLUSION_PATTERNS = (
    # Auto-virements (Lorène vers ses autres comptes)
    ("vers compte joint", "Auto-virement vers compte joint"),
    ("vers compte fortuneo", "Auto-virement vers compte Fortuneo"),
    ("vers compte epargne", "Auto-virement vers compte épargne"),
    # Remboursements bancaires
    ("regul remboursement", "Remboursement bancaire"),
    ("avoir carte", "Avoir carte"),
    # Administrations (au crédit = remboursement)
    ("drfip", "Remboursement administration (DRFIP)"),
    ("dgfip", "Remboursement administration (DGFIP)"),
    ("urssaf", "Remboursement URSSAF"),
    ("caf ", "Remboursement CAF"),
    ("pole emploi", "Pôle Emploi"),
    ("impots", "Remboursement impôts"),
    # CARPA : décision de Lorène — ce ne sont pas des honoraires à facturer
    # (mouvements de fonds de tiers via la Caisse des Règlements Pécuniaires),
    # donc exclus du périmètre de facturation.
    ("carpa", "CARPA — mouvement de fonds, non facturable (décision métier)"),
)

# ─── Cas spécifiques "à valider" (inclus mais demande validation humaine) ───
# Le LLM ne peut pas trancher seul, l'utilisateur doit décider.
# (Vide pour l'instant : CARPA a été déplacé en exclusion.)

_REVIEW_PATTERNS: tuple[tuple[str, str], ...] = ()


@dataclass
class FilterDecision:
    est_virement_entrant: bool
    review_status: str  # 'a_valider' | 'exclu'
    raison: str


def _matches_prefix(libelle_lower: str, prefixes: tuple[str, ...]) -> bool:
    return any(libelle_lower.startswith(p) for p in prefixes)


def _matches_pattern(libelle_lower: str, patterns: tuple[tuple[str, str], ...]) -> tuple[bool, str]:
    for pat, raison in patterns:
        if pat in libelle_lower:
            return True, raison
    return False, ""


def filter_transaction(libelle: str, sens: str) -> FilterDecision:
    """Décide si une transaction est un virement entrant candidat à facturer."""
    # Règle 0 : un débit n'est jamais un virement entrant
    if sens != "credit":
        return FilterDecision(
            est_virement_entrant=False,
            review_status="exclu",
            raison="Débit, non concerné",
        )

    libelle_lower = libelle.lower()

    # Règle 1 : exclusion forte
    excluded, raison_ex = _matches_pattern(libelle_lower, _EXCLUSION_PATTERNS)
    if excluded:
        return FilterDecision(
            est_virement_entrant=False,
            review_status="exclu",
            raison=raison_ex,
        )

    # Règle 2 : cas spéciaux à valider (CARPA en l'état)
    review, raison_rv = _matches_pattern(libelle_lower, _REVIEW_PATTERNS)
    if review:
        return FilterDecision(
            est_virement_entrant=True,
            review_status="a_valider",
            raison=raison_rv,
        )

    # Règle 3 : préfixes de virement
    if _matches_prefix(libelle_lower, _VIREMENT_PREFIXES):
        return FilterDecision(
            est_virement_entrant=True,
            review_status="a_valider",
            raison="Virement entrant à valider",
        )

    # Règle 4 : crédit qui ne matche aucun préfixe virement
    # (peut être une remise de chèque, un encaissement spécial, etc.)
    # → On exclut, l'utilisateur pourra l'inclure manuellement depuis la UI.
    return FilterDecision(
        est_virement_entrant=False,
        review_status="exclu",
        raison="Crédit non identifié comme virement (chèque, espèces ?)",
    )
