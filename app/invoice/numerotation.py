"""Numérotation continue des factures, PAR USER.

Chaque user a sa propre séquence : `2026-0001` pour Lorène ET pour toi, sans
conflit. Le compteur est stocké dans la table Config avec une clé qui inclut
l'user_id (ex: `numero_compteur_2026_user42`).

Format : AAAA-NNNN. Reset annuel. Continue au sein d'une année.
"""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.models import Config, Facture


def _key(annee: int, user_id: int) -> str:
    return f"numero_compteur_{annee}_user{user_id}"


def _format_numero(annee: int, compteur: int) -> str:
    return f"{annee}-{compteur:04d}"


def _get_compteur(session: Session, annee: int, user_id: int) -> int:
    row = session.get(Config, _key(annee, user_id))
    return int(row.valeur) if row else 0


def _set_compteur(session: Session, annee: int, user_id: int, value: int) -> None:
    k = _key(annee, user_id)
    row = session.get(Config, k)
    if row is None:
        row = Config(cle=k, valeur=str(value))
    else:
        row.valeur = str(value)
    session.add(row)


def peek_prochain_numero(session: Session, user_id: int, date_emission: date | None = None) -> str:
    annee = (date_emission or date.today()).year
    return _format_numero(annee, _get_compteur(session, annee, user_id) + 1)


def attribuer_numero(session: Session, user_id: int, date_emission: date | None = None) -> str:
    """Attribue et réserve le prochain numéro de facture pour ce user."""
    annee = (date_emission or date.today()).year
    compteur = _get_compteur(session, annee, user_id) + 1
    numero = _format_numero(annee, compteur)
    _set_compteur(session, annee, user_id, compteur)
    return numero


def numero_deja_utilise(session: Session, user_id: int, numero: str) -> bool:
    existing = session.exec(
        select(Facture).where(
            Facture.numero == numero,
            Facture.user_id == user_id,
        )
    ).first()
    return existing is not None
