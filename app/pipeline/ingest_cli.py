"""CLI Lot 2 : ingère un ou plusieurs PDF en base.

Usage :
    python -m app.pipeline.ingest_cli data/pdfs/releve_ca_2026_01.pdf
    python -m app.pipeline.ingest_cli data/pdfs/*.pdf
    python -m app.pipeline.ingest_cli --no-llm data/pdfs/releve.pdf

Effets :
  - Crée/met à jour la base SQLite (releves, transactions)
  - Idempotent : si le PDF a déjà été ingéré (par hash), affiche un message
    et ne recommence pas
  - Affiche un rapport en fin d'exécution
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlmodel import Session

from app.db import engine, init_db
from app.llm.factory import get_llm_client
from app.pipeline.ingest import IngestResult, ingest_pdf

STATUS_ICONS = {
    "created": "✓",
    "already_present": "=",
    "quarantine": "⚠",
    "error": "✗",
}


def _print_result(result: IngestResult) -> None:
    icon = STATUS_ICONS.get(result.status, "?")
    print(f"\n{icon} {result.pdf_path.name}")
    print(f"  Statut : {result.status}")
    if result.banque:
        print(f"  Banque : {result.banque}")
    if result.releve_id is not None:
        print(f"  Relevé id : {result.releve_id}")
    print(f"  Transactions : {result.nb_transactions}")
    for err in result.errors:
        print(f"    ✗ {err}")
    for warn in result.warnings[:3]:  # limite l'affichage
        print(f"    ⚠ {warn}")
    if len(result.warnings) > 3:
        print(f"    ⚠ ... +{len(result.warnings) - 3} autres warning(s)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Ingère des PDFs de relevés en base.")
    parser.add_argument("pdfs", nargs="+", type=Path, help="Chemins des PDFs")
    parser.add_argument(
        "--user-id", type=int, required=True,
        help="ID du user destinataire (lister via `python -m app.cli adduser` ou via l'UI)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="N'utilise pas le LLM pour l'extraction d'émetteur",
    )
    args = parser.parse_args(argv[1:])

    init_db()

    # Vérifier que l'user existe
    from sqlmodel import select
    from app.models import User
    with Session(engine) as session:
        user = session.get(User, args.user_id)
        if user is None:
            print(f"✗ User #{args.user_id} introuvable.")
            print("  Liste : python -m app.cli (puis va sur /clients pour voir les users)")
            return 2
        print(f"→ Ingestion pour {user.email} (#{user.id})")

    llm = None
    if not args.no_llm:
        try:
            llm = get_llm_client()
            print(f"→ LLM activé : {type(llm).__name__}")
        except Exception as e:
            print(f"⚠ LLM indisponible ({e}), ingestion sans extraction émetteur")
            llm = None
    else:
        print("→ LLM désactivé (--no-llm)")

    # Ingestion
    results: list[IngestResult] = []
    with Session(engine) as session:
        for pdf_path in args.pdfs:
            print(f"\n→ Ingestion : {pdf_path}")
            result = ingest_pdf(pdf_path, session, user_id=args.user_id, llm=llm)
            results.append(result)
            _print_result(result)

    # Rapport final
    print("\n" + "=" * 60)
    print("Rapport final")
    print("=" * 60)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    for status, count in by_status.items():
        icon = STATUS_ICONS.get(status, "?")
        print(f"  {icon} {status:<20}  {count}")

    # Code de sortie : 0 si tout est OK, 1 sinon
    has_error = any(r.status in ("error", "quarantine") for r in results)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
