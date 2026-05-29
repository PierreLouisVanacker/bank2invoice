"""CLI Lot 2 : inspecte la base après ingestion.

Usage :
    python -m app.pipeline.inspect_cli releves          # liste les relevés
    python -m app.pipeline.inspect_cli transactions <releve_id>
    python -m app.pipeline.inspect_cli entrants         # tous les virements entrants candidats
    python -m app.pipeline.inspect_cli entrants <releve_id>
"""

from __future__ import annotations

import sys
from typing import Optional

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Releve, Transaction


def _format_money(d) -> str:
    if d is None:
        return ""
    return f"{d:>10}"


def cmd_releves() -> None:
    with Session(engine) as session:
        releves = session.exec(select(Releve).order_by(Releve.uploaded_at.desc())).all()
        if not releves:
            print("(Aucun relevé en base. Lance d'abord `python -m app.pipeline.ingest_cli ...`)")
            return

        print(f"{'ID':>4}  {'Fichier':<40}  {'Banque':<5}  {'Période':<25}  {'Statut':<15}")
        print(f"{'-'*4}  {'-'*40}  {'-'*5}  {'-'*25}  {'-'*15}")
        for r in releves:
            periode = f"{r.date_debut} → {r.date_fin}" if r.date_debut else "—"
            print(
                f"{r.id:>4}  "
                f"{r.nom_fichier[:40]:<40}  "
                f"{(r.banque or '—'):<5}  "
                f"{periode:<25}  "
                f"{r.statut:<15}"
            )


def cmd_transactions(releve_id: int) -> None:
    with Session(engine) as session:
        releve = session.get(Releve, releve_id)
        if releve is None:
            print(f"Relevé id={releve_id} introuvable.")
            sys.exit(1)

        txs = session.exec(
            select(Transaction).where(Transaction.releve_id == releve_id).order_by(Transaction.date)
        ).all()

        print(f"Relevé #{releve_id} — {releve.nom_fichier} ({len(txs)} transactions)")
        print()
        print(
            f"{'Date':<10}  {'Sens':<6}  {'Montant':>10}  "
            f"{'Entrant':<7}  {'Status':<11}  Libellé"
        )
        print("-" * 100)
        for tx in txs:
            entrant = "✓" if tx.est_virement_entrant else ""
            print(
                f"{tx.date.isoformat():<10}  "
                f"{tx.sens:<6}  "
                f"{tx.montant:>10}  "
                f"{entrant:<7}  "
                f"{tx.review_status:<11}  "
                f"{tx.libelle_brut[:50]}"
            )


def cmd_entrants(releve_id: Optional[int] = None) -> None:
    with Session(engine) as session:
        stmt = select(Transaction).where(Transaction.est_virement_entrant == True)  # noqa: E712
        if releve_id is not None:
            stmt = stmt.where(Transaction.releve_id == releve_id)
        stmt = stmt.order_by(Transaction.date)

        txs = session.exec(stmt).all()

        if not txs:
            print("Aucun virement entrant en base.")
            return

        title = f"Virements entrants (relevé #{releve_id})" if releve_id else "Virements entrants (tous relevés)"
        print(title)
        print()
        print(
            f"{'ID':>4}  {'Date':<10}  {'Montant':>10}  "
            f"{'Émetteur':<35}  {'Type':<8}  {'Client':<8}  {'Status':<11}"
        )
        print("-" * 110)
        for tx in txs:
            if tx.emetteur_type == "personne":
                emetteur = " ".join(
                    filter(None, [tx.emetteur_civilite, tx.emetteur_prenom, tx.emetteur_nom])
                )
            elif tx.emetteur_type == "societe":
                emetteur = tx.emetteur_raison_sociale or ""
            else:
                emetteur = "(non extrait)"

            client_str = f"#{tx.client_id}" if tx.client_id else "—"
            print(
                f"{tx.id:>4}  "
                f"{tx.date.isoformat():<10}  "
                f"{tx.montant:>10}  "
                f"{emetteur[:35]:<35}  "
                f"{(tx.emetteur_type or '—'):<8}  "
                f"{client_str:<8}  "
                f"{tx.review_status:<11}"
            )

            # Affiche le libellé brut en sous-ligne pour debug
            print(f"      └─ {tx.libelle_brut[:90]}")


def main(argv: list[str]) -> int:
    init_db()

    if len(argv) < 2:
        print("Usage :")
        print("  python -m app.pipeline.inspect_cli releves")
        print("  python -m app.pipeline.inspect_cli transactions <releve_id>")
        print("  python -m app.pipeline.inspect_cli entrants [releve_id]")
        return 2

    cmd = argv[1]
    if cmd == "releves":
        cmd_releves()
    elif cmd == "transactions":
        if len(argv) < 3:
            print("Manque <releve_id>")
            return 2
        cmd_transactions(int(argv[2]))
    elif cmd == "entrants":
        releve_id = int(argv[2]) if len(argv) >= 3 else None
        cmd_entrants(releve_id)
    else:
        print(f"Commande inconnue : {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
