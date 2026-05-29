"""Validations déterministes appliquées après parsing d'un relevé.

Ces validations vérifient la cohérence du résultat avec les totaux et soldes
affichés sur le relevé. Si une validation échoue, le pipeline doit refuser de
générer des factures à partir de ce relevé (statut "quarantaine").

Tolérance : 1 centime sur la somme des montants, pour absorber d'éventuels
arrondis de représentation (rare avec des Decimal mais on garde la marge).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.pipeline.parse_ca import ParsedReleve

# Tolérance d'arrondi (cents) — 0.01 est large, en pratique on tombe sur du 0.00
TOLERANCE = Decimal("0.01")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Statistiques calculées pour debug
    computed_total_debits: Decimal | None = None
    computed_total_credits: Decimal | None = None
    computed_solde_final: Decimal | None = None


def validate(releve: ParsedReleve) -> ValidationResult:
    """Vérifie la cohérence des transactions extraites avec les soldes/totaux."""
    result = ValidationResult(ok=True)
    meta = releve.metadata

    # Somme des débits/crédits extraits
    total_debits = sum(
        (tx.montant for tx in releve.transactions if tx.sens == "debit"),
        Decimal("0"),
    )
    total_credits = sum(
        (tx.montant for tx in releve.transactions if tx.sens == "credit"),
        Decimal("0"),
    )
    result.computed_total_debits = total_debits
    result.computed_total_credits = total_credits

    # ─── Validation 1 : totaux ───
    if meta.total_debits is not None:
        diff = abs(total_debits - meta.total_debits)
        if diff > TOLERANCE:
            result.errors.append(
                f"Total débits extrait ({total_debits}) ≠ total relevé "
                f"({meta.total_debits}), différence = {diff}"
            )

    if meta.total_credits is not None:
        diff = abs(total_credits - meta.total_credits)
        if diff > TOLERANCE:
            result.errors.append(
                f"Total crédits extrait ({total_credits}) ≠ total relevé "
                f"({meta.total_credits}), différence = {diff}"
            )

    # ─── Validation 2 : solde final cohérent ───
    if meta.solde_initial is not None and meta.solde_final is not None:
        computed = meta.solde_initial + total_credits - total_debits
        result.computed_solde_final = computed
        diff = abs(computed - meta.solde_final)
        if diff > TOLERANCE:
            result.errors.append(
                f"Solde final calculé ({computed}) ≠ solde relevé ({meta.solde_final}), "
                f"différence = {diff}. Une transaction est probablement manquante "
                f"ou mal extraite."
            )

    # ─── Validation 3 : dates dans la période ───
    if meta.date_debut and meta.date_fin:
        out_of_range = [
            tx for tx in releve.transactions
            if not (meta.date_debut <= tx.date_operation <= meta.date_fin)
        ]
        if out_of_range:
            # On signale en warning (pas bloquant — peut arriver si on est au pivot
            # d'une année et qu'on a deviné la mauvaise année pour une transaction).
            for tx in out_of_range[:3]:  # max 3 pour pas spammer
                result.warnings.append(
                    f"Date hors période : {tx.date_operation} "
                    f"(période {meta.date_debut}..{meta.date_fin}) — "
                    f"libellé: {tx.libelle[:60]!r}"
                )

    # ─── Validation 4 : sanity checks de base ───
    if not releve.transactions:
        result.errors.append("Aucune transaction extraite du relevé.")

    if meta.solde_initial is None:
        result.warnings.append("Solde initial non détecté dans le texte du relevé.")
    if meta.solde_final is None:
        result.warnings.append("Solde final non détecté dans le texte du relevé.")

    # Propager les warnings du parser
    result.warnings.extend(releve.warnings)

    result.ok = not result.errors
    return result
