"""Service d'ingestion : persiste un PDF de relevé en base de données.

Pipeline :
  1. Calcul du hash SHA256 du fichier → idempotence
  2. Extraction du texte pour détecter la banque
  3. Parsing (selon la banque) → ParsedReleve
  4. Validations déterministes
  5. Si validations OK : insertion Releve + Transactions en base
  6. Pour chaque transaction :
     a. Filtrage : virement entrant candidat ou non ?
     b. Si candidat : extraction émetteur via LLM
     c. Matching client par aliases existants
  7. Commit final
  Si validations KO en étape 4 : Releve en statut 'quarantaine', pas de tx.

Retour : un IngestResult qui décrit ce qui s'est passé.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from sqlmodel import Session, select

from app.llm.base import LLMClient
from app.models import Releve, Transaction
from app.pipeline import parse_ca
from app.pipeline.detect_banque import detect_banque
from app.pipeline.emetteur import extract_emetteur
from app.pipeline.filter_entrants import filter_transaction
from app.pipeline.match_client import find_client_for_transaction
from app.pipeline.parse_ca import ParsedReleve
from app.pipeline.validate import ValidationResult, validate

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Résultat d'une ingestion de PDF."""

    status: str  # 'created' | 'already_present' | 'quarantine' | 'error'
    pdf_path: Path
    releve_id: int | None = None
    banque: str | None = None
    nb_transactions: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation: ValidationResult | None = None


def _compute_hash(pdf_path: Path) -> str:
    """SHA256 du fichier, en hex."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_full_text(pdf_path: Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


# Registre des parsers par banque. Pour l'instant CA uniquement.
# Tous renvoient un ParsedReleve.
_PARSERS = {
    "CA": parse_ca.parse,
}


def ingest_pdf(
    pdf_path: Path,
    session: Session,
    user_id: int,
    llm: LLMClient | None = None,
) -> IngestResult:
    """Ingère un PDF de relevé pour le user donné. Idempotent par (user, hash).

    Si `llm` est fourni, l'extraction d'émetteur est faite pour chaque
    virement entrant candidat.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return IngestResult(
            status="error",
            pdf_path=pdf_path,
            errors=[f"Fichier introuvable : {pdf_path}"],
        )

    # 1. Hash & idempotence (par user : deux users peuvent uploader le même PDF)
    file_hash = _compute_hash(pdf_path)
    existing = session.exec(
        select(Releve).where(
            Releve.hash_fichier == file_hash,
            Releve.user_id == user_id,
        )
    ).first()
    if existing is not None:
        nb_tx = len(session.exec(
            select(Transaction).where(Transaction.releve_id == existing.id)
        ).all())
        return IngestResult(
            status="already_present",
            pdf_path=pdf_path,
            releve_id=existing.id,
            banque=existing.banque,
            nb_transactions=nb_tx,
        )

    # 2. Détection banque
    text = _extract_full_text(pdf_path)
    banque = detect_banque(text)

    if banque is None or banque not in _PARSERS:
        # Banque inconnue : on enregistre le relevé en quarantaine pour ne pas
        # le re-traiter si l'utilisateur le réimporte par erreur.
        releve = Releve(user_id=user_id,
            nom_fichier=pdf_path.name,
            banque=banque,
            hash_fichier=file_hash,
            statut="quarantaine",
        )
        session.add(releve)
        session.commit()
        session.refresh(releve)
        return IngestResult(
            status="quarantine",
            pdf_path=pdf_path,
            releve_id=releve.id,
            banque=banque,
            errors=[
                f"Banque '{banque}' non supportée. Seul CA est implémenté."
                if banque
                else "Banque non identifiée dans le PDF."
            ],
        )

    # 3. Parsing
    parser = _PARSERS[banque]
    try:
        parsed: ParsedReleve = parser(pdf_path)
    except Exception as e:
        # Erreur de parsing inattendue : on quarantine
        releve = Releve(user_id=user_id,
            nom_fichier=pdf_path.name,
            banque=banque,
            hash_fichier=file_hash,
            statut="quarantaine",
        )
        session.add(releve)
        session.commit()
        session.refresh(releve)
        return IngestResult(
            status="error",
            pdf_path=pdf_path,
            releve_id=releve.id,
            banque=banque,
            errors=[f"Erreur de parsing : {type(e).__name__}: {e}"],
        )

    # 4. Validations
    validation = validate(parsed)

    if not validation.ok:
        releve = Releve(user_id=user_id,
            nom_fichier=pdf_path.name,
            banque=banque,
            date_debut=parsed.metadata.date_debut,
            date_fin=parsed.metadata.date_fin,
            hash_fichier=file_hash,
            statut="quarantaine",
        )
        session.add(releve)
        session.commit()
        session.refresh(releve)
        return IngestResult(
            status="quarantine",
            pdf_path=pdf_path,
            releve_id=releve.id,
            banque=banque,
            errors=validation.errors,
            warnings=validation.warnings,
            validation=validation,
        )

    # 5. Insertion en base
    releve = Releve(user_id=user_id,
        nom_fichier=pdf_path.name,
        banque=banque,
        date_debut=parsed.metadata.date_debut,
        date_fin=parsed.metadata.date_fin,
        hash_fichier=file_hash,
        statut="parse",
    )
    session.add(releve)
    session.commit()
    session.refresh(releve)

    for parsed_tx in parsed.transactions:
        # Étape 6a : filtrage
        decision = filter_transaction(parsed_tx.libelle, parsed_tx.sens)

        tx = Transaction(
            releve_id=releve.id,
            date=parsed_tx.date_operation,
            libelle_brut=parsed_tx.libelle,
            montant=parsed_tx.montant,
            sens=parsed_tx.sens,
            est_virement_entrant=decision.est_virement_entrant,
            review_status=decision.review_status,
        )

        # Étapes 6b et 6c : seulement pour les virements entrants candidats
        if decision.est_virement_entrant:
            if llm is not None:
                try:
                    emetteur = extract_emetteur(parsed_tx.libelle, llm)
                    tx.emetteur_type = emetteur.type
                    tx.emetteur_civilite = emetteur.civilite
                    tx.emetteur_nom = emetteur.nom
                    tx.emetteur_prenom = emetteur.prenom
                    tx.emetteur_raison_sociale = emetteur.raison_sociale

                    # Matching client par alias + nom
                    client_id = find_client_for_transaction(
                        parsed_tx.libelle, emetteur, session
                    )
                    tx.client_id = client_id
                except Exception as e:
                    logger.warning(
                        "Extraction émetteur échouée pour %r: %s",
                        parsed_tx.libelle[:60], e,
                    )
                    tx.emetteur_type = "inconnu"
            else:
                # Pas de LLM fourni : champs émetteur restent NULL
                tx.emetteur_type = None

        session.add(tx)

    session.commit()

    return IngestResult(
        status="created",
        pdf_path=pdf_path,
        releve_id=releve.id,
        banque=banque,
        nb_transactions=len(parsed.transactions),
        warnings=validation.warnings,
        validation=validation,
    )
