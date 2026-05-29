"""Matching d'une transaction à un client existant en base.

Stratégie en 3 passes, par ordre de priorité :

  1. Match par alias exact : on cherche dans `client_aliases` un alias dont la
     chaîne est contenue dans le libellé brut. Le plus haut `poids` gagne.
     C'est la mémoire incrémentale de l'outil : chaque libellé validé devient
     un alias.

  2. Match par nom+prénom (personne) ou raison_sociale (société) après
     extraction émetteur. Tolère casse différente, espaces.

  3. Pas de match → suggérer "Nouveau client" (client_id reste NULL).

L'opération ne crée AUCUN client. La création se fait dans la UI au lot 3
quand l'utilisateur valide ou crée explicitement un client.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.llm.base import EmetteurExtrait
from app.models import Client, ClientAlias


def find_client_for_transaction(
    libelle_brut: str,
    emetteur: EmetteurExtrait,
    session: Session,
) -> int | None:
    """Renvoie le client_id le plus probable, ou None si pas de match."""

    # Passe 1 : match par alias
    client_id = _match_by_alias(libelle_brut, session)
    if client_id is not None:
        return client_id

    # Passe 2 : match par nom/prénom ou raison_sociale
    if emetteur.type == "personne":
        return _match_by_personne(emetteur.nom, emetteur.prenom, session)
    if emetteur.type == "societe":
        return _match_by_societe(emetteur.raison_sociale, session)

    return None


def _match_by_alias(libelle_brut: str, session: Session) -> int | None:
    """Cherche un alias dont la chaîne est contenue dans le libellé brut.

    En cas de plusieurs matches, le plus haut `poids` gagne (alias le plus
    fréquemment utilisé).
    """
    libelle_lower = libelle_brut.lower()
    aliases = session.exec(select(ClientAlias).order_by(ClientAlias.poids.desc())).all()

    for alias in aliases:
        if alias.alias_libelle.lower() in libelle_lower:
            return alias.client_id

    return None


def _normalize(s: str | None) -> str:
    return (s or "").strip().lower()


def _match_by_personne(
    nom: str | None, prenom: str | None, session: Session
) -> int | None:
    """Cherche un client personne avec ce nom (+ idéalement prénom)."""
    if not nom:
        return None

    nom_norm = _normalize(nom)
    candidates = session.exec(
        select(Client).where(Client.type == "personne")
    ).all()

    # 1) Match nom + prénom
    if prenom:
        prenom_norm = _normalize(prenom)
        for c in candidates:
            if (
                _normalize(c.nom) == nom_norm
                and _normalize(c.prenom) == prenom_norm
            ):
                return c.id

    # 2) Match nom seul (si unique)
    matches_nom = [c for c in candidates if _normalize(c.nom) == nom_norm]
    if len(matches_nom) == 1:
        return matches_nom[0].id

    # Plusieurs personnes avec le même nom → on ne tranche pas
    return None


def _match_by_societe(
    raison_sociale: str | None, session: Session
) -> int | None:
    """Cherche un client société avec cette raison sociale."""
    if not raison_sociale:
        return None

    rs_norm = _normalize(raison_sociale)
    candidates = session.exec(
        select(Client).where(Client.type == "societe")
    ).all()

    for c in candidates:
        if _normalize(c.raison_sociale) == rs_norm:
            return c.id

    return None
