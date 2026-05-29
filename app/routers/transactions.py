"""Router transactions : édition inline HTMX + panneau détail.

Multi-utilisateurs : une transaction est accessible UNIQUEMENT si son relevé
appartient au user connecté. On vérifie l'ownership à chaque endpoint.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.auth.deps import current_user
from app.db import get_session
from app.models import Client, ClientAlias, Releve, Transaction, User

router = APIRouter(prefix="/transactions", tags=["transactions"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_tx_for(tx_id: int, user: User, session: Session) -> Transaction:
    """Charge une transaction en vérifiant que son relevé appartient au user."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction introuvable")
    releve = session.get(Releve, tx.releve_id)
    if releve is None or releve.user_id != user.id:
        raise HTTPException(status_code=404, detail="Transaction introuvable")
    return tx


def _user_clients(user: User, session: Session) -> list[Client]:
    return session.exec(
        select(Client).where(Client.user_id == user.id).order_by(Client.nom)
    ).all()


def _validate_client_ownership(client_id: int | None, user: User, session: Session) -> int | None:
    """Vérifie que le client_id appartient bien au user. None si non valide."""
    if client_id is None:
        return None
    c = session.get(Client, client_id)
    if c is None or c.user_id != user.id:
        return None
    return client_id


def _row_response(
    tx: Transaction,
    request: Request,
    session: Session,
    user: User,
    count_inclus: int | None = None,
) -> HTMLResponse:
    clients = _user_clients(user, session)
    return templates.TemplateResponse(
        request,
        "transactions/_row.html",
        {"tx": tx, "clients": clients, "count_inclus": count_inclus},
    )


def _maybe_create_alias(tx: Transaction, session: Session) -> None:
    if tx.client_id is None:
        return
    libelle = tx.libelle_brut
    cleaned = re.sub(
        r"^(virement\s+(?:vir\s+inst\s+)?(?:de\s+)?|vir\s+sepa\s+)",
        "", libelle, flags=re.IGNORECASE,
    )
    cleaned = cleaned.split("/", 1)[0].strip()
    alias_text = cleaned[:80].strip()
    if not alias_text:
        return

    existing = session.exec(
        select(ClientAlias).where(
            ClientAlias.client_id == tx.client_id,
            ClientAlias.alias_libelle == alias_text,
        )
    ).first()
    if existing:
        existing.poids += 1
        session.add(existing)
    else:
        session.add(ClientAlias(client_id=tx.client_id, alias_libelle=alias_text, poids=1))


# ─── Endpoints ──────────────────────────────────────────────────────────────


@router.post("/{tx_id}/inclure", response_class=HTMLResponse)
def toggle_inclure(
    tx_id: int,
    request: Request,
    inclus: str = Form(default=""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    tx = _get_tx_for(tx_id, user, session)
    tx.inclus = bool(inclus)
    tx.review_status = "valide" if tx.inclus else "a_valider"
    session.add(tx)
    session.commit()
    session.refresh(tx)

    # Recompte le nombre d'inclus pour CE relevé
    count_inclus = len(session.exec(
        select(Transaction).where(
            Transaction.releve_id == tx.releve_id,
            Transaction.inclus == True,  # noqa: E712
        )
    ).all())

    return _row_response(tx, request, session, user, count_inclus=count_inclus)


@router.patch("/{tx_id}", response_class=HTMLResponse)
def patch_emetteur_type(
    tx_id: int,
    request: Request,
    emetteur_type: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    tx = _get_tx_for(tx_id, user, session)
    tx.emetteur_type = emetteur_type or None
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return _row_response(tx, request, session, user)


@router.patch("/{tx_id}/client", response_class=HTMLResponse)
def patch_client(
    tx_id: int,
    request: Request,
    client_id: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    tx = _get_tx_for(tx_id, user, session)
    new_id = _validate_client_ownership(int(client_id) if client_id else None, user, session)
    tx.client_id = new_id
    session.add(tx)
    if new_id is not None:
        _maybe_create_alias(tx, session)
    session.commit()
    session.refresh(tx)
    return _row_response(tx, request, session, user)


@router.get("/{tx_id}/panel", response_class=HTMLResponse)
def get_panel(
    tx_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    tx = _get_tx_for(tx_id, user, session)
    return templates.TemplateResponse(
        request, "transactions/_panel.html",
        {"tx": tx, "clients": _user_clients(user, session)},
    )


@router.post("/{tx_id}/details", response_class=HTMLResponse)
def save_details(
    tx_id: int,
    request: Request,
    emetteur_type: str = Form(""),
    emetteur_civilite: str = Form(""),
    emetteur_prenom: str = Form(""),
    emetteur_nom: str = Form(""),
    emetteur_raison_sociale: str = Form(""),
    client_id: str = Form(""),
    date_facture: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    tx = _get_tx_for(tx_id, user, session)

    tx.emetteur_type = emetteur_type or None
    tx.emetteur_civilite = emetteur_civilite or None
    tx.emetteur_prenom = emetteur_prenom or None
    tx.emetteur_nom = emetteur_nom or None
    tx.emetteur_raison_sociale = emetteur_raison_sociale or None

    if date_facture:
        from datetime import date as _date
        try:
            parsed = _date.fromisoformat(date_facture)
            tx.date_facture_override = None if parsed == tx.date else parsed
        except ValueError:
            pass

    new_id = _validate_client_ownership(int(client_id) if client_id else None, user, session)
    client_changed = tx.client_id != new_id
    tx.client_id = new_id

    session.add(tx)
    if client_changed and new_id is not None:
        _maybe_create_alias(tx, session)

    session.commit()
    session.refresh(tx)
    return _row_response(tx, request, session, user)


@router.post("/{tx_id}/create-client", response_class=HTMLResponse)
def create_client_from_tx(
    tx_id: int,
    request: Request,
    emetteur_type: str = Form(""),
    emetteur_civilite: str = Form(""),
    emetteur_prenom: str = Form(""),
    emetteur_nom: str = Form(""),
    emetteur_raison_sociale: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Crée un client pour le user connecté à partir de l'émetteur saisi."""
    tx = _get_tx_for(tx_id, user, session)

    tx.emetteur_type = emetteur_type or None
    tx.emetteur_civilite = emetteur_civilite or None
    tx.emetteur_prenom = emetteur_prenom or None
    tx.emetteur_nom = emetteur_nom or None
    tx.emetteur_raison_sociale = emetteur_raison_sociale or None

    if emetteur_type == "societe":
        client_type = "societe"
        nom = (emetteur_raison_sociale or "").strip()
        raison_sociale = nom
        prenom = None
    else:
        client_type = "personne"
        nom = (emetteur_nom or "").strip()
        prenom = (emetteur_prenom or "").strip() or None
        raison_sociale = None

    if not nom:
        return templates.TemplateResponse(
            request, "transactions/_panel.html",
            {
                "tx": tx,
                "clients": _user_clients(user, session),
                "error": "Renseigne au moins un nom (ou une raison sociale) avant de créer le client.",
            },
        )

    client = Client(
        user_id=user.id,
        type=client_type,
        nom=nom, prenom=prenom, raison_sociale=raison_sociale,
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    tx.client_id = client.id
    session.add(tx)
    _maybe_create_alias(tx, session)
    session.commit()
    session.refresh(tx)

    return templates.TemplateResponse(
        request, "transactions/_panel.html",
        {
            "tx": tx,
            "clients": _user_clients(user, session),
            "flash": f"Client « {nom} » créé et associé.",
        },
    )
