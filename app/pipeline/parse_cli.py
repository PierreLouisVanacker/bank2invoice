"""CLI Lot 1 : parse un PDF de relevé bancaire et affiche le résultat.

Usage :
    python -m app.pipeline.parse_cli data/pdfs/releve_ca_2026_01.pdf
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from app.pipeline import parse_ca
from app.pipeline.detect_banque import detect_banque
from app.pipeline.validate import validate


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _extract_full_text(pdf_path: Path) -> str:
    """Concatène le texte de toutes les pages, pour la détection de banque."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage : python -m app.pipeline.parse_cli <chemin_pdf>")
        return 2

    pdf_path = Path(argv[1])
    if not pdf_path.exists():
        print(f"✗ Fichier introuvable : {pdf_path}")
        return 2

    print(f"→ Lecture du PDF : {pdf_path}")
    text = _extract_full_text(pdf_path)
    banque = detect_banque(text)
    print(f"→ Banque détectée : {banque or 'INCONNUE'}")

    if banque != "CA":
        print(
            f"✗ Pour l'instant, seul le Crédit Agricole est supporté au Lot 1. "
            f"(Détecté : {banque})"
        )
        return 1

    print("→ Parsing des transactions…")
    releve = parse_ca.parse(pdf_path)

    meta = releve.metadata
    print()
    print("Métadonnées :")
    print(f"  Période       : {meta.date_debut} → {meta.date_fin}")
    print(f"  Solde initial : {meta.solde_initial}")
    print(f"  Solde final   : {meta.solde_final}")
    print(f"  Total débits  : {meta.total_debits}")
    print(f"  Total crédits : {meta.total_credits}")
    print(f"  Nb transactions extraites : {len(releve.transactions)}")

    print()
    print("Transactions :")
    print(f"  {'Date':<10}  {'Libellé':<60}  {'Sens':<7}  {'Montant':>10}")
    print(f"  {'-'*10}  {'-'*60}  {'-'*7}  {'-'*10}")
    for tx in releve.transactions:
        print(
            f"  {tx.date_operation.isoformat():<10}  "
            f"{tx.libelle[:60]:<60}  "
            f"{tx.sens:<7}  "
            f"{tx.montant:>10}"
        )

    print()
    print("→ Validations…")
    v = validate(releve)
    if v.warnings:
        print(f"  ⚠ {len(v.warnings)} warning(s) :")
        for w in v.warnings:
            print(f"    - {w}")
    if not v.ok:
        print(f"  ✗ {len(v.errors)} erreur(s) :")
        for e in v.errors:
            print(f"    - {e}")
    else:
        print("  ✓ Toutes les validations passent")
        print(f"    Total débits calculé : {v.computed_total_debits}")
        print(f"    Total crédits calculé : {v.computed_total_credits}")
        if v.computed_solde_final is not None:
            print(f"    Solde final calculé : {v.computed_solde_final}")

    # Export JSON
    output_dir = Path("data/parsed")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{pdf_path.stem}.json"

    out_data = {
        "pdf": str(pdf_path),
        "banque": banque,
        "metadata": {
            "date_debut": meta.date_debut,
            "date_fin": meta.date_fin,
            "solde_initial": meta.solde_initial,
            "solde_final": meta.solde_final,
            "total_debits": meta.total_debits,
            "total_credits": meta.total_credits,
        },
        "transactions": [asdict(tx) for tx in releve.transactions],
        "validation": {
            "ok": v.ok,
            "errors": v.errors,
            "warnings": v.warnings,
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2, default=_json_default)

    print()
    print(f"→ JSON écrit : {out_path}")
    return 0 if v.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
