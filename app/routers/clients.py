"""Router clients : liste + création + édition.

Multi-utilisateurs : chaque user ne voit que ses clients.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.auth.deps import current_user
from app.db import get_session
from app.models import Client, ClientAlias, User

router = APIRouter(prefix="/clients", tags=["clients"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _none_if_empty(s: str) -> str | None:
    s = (s or "").strip()
    return s if s else None


def _get_client_for(client_id: int, user: User, session: Session) -> Client:
    c = session.get(Client, client_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="Client introuvable")
    return c


@router.get("", response_class=HTMLResponse)
def list_clients(
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    clients = session.exec(
        select(Client).where(Client.user_id == user.id).order_by(Client.nom)
    ).all()
    aliases_count: dict[int, int] = {c.id: 0 for c in clients}
    aliases = session.exec(
        select(ClientAlias).where(ClientAlias.client_id.in_([c.id for c in clients]) if clients else False)
    ).all()
    for a in aliases:
        aliases_count[a.client_id] = aliases_count.get(a.client_id, 0) + 1

    return templates.TemplateResponse(
        request, "clients/list.html",
        {"clients": clients, "aliases_count": aliases_count, "user": user},
    )


@router.get("/new", response_class=HTMLResponse)
def new_client_form(
    request: Request,
    user: User = Depends(current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "clients/_form.html", {"client": None},
    )


@router.post("/new", response_class=HTMLResponse)
def create_client(
    request: Request,
    type: str = Form(...),
    nom: str = Form(...),
    prenom: str = Form(""),
    raison_sociale: str = Form(""),
    adresse: str = Form(""),
    code_postal: str = Form(""),
    ville: str = Form(""),
    email: str = Form(""),
    siret: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    if type not in ("personne", "societe"):
        raise HTTPException(status_code=400, detail="type doit être 'personne' ou 'societe'")

    client = Client(
        user_id=user.id,
        type=type,
        nom=nom.strip(),
        prenom=_none_if_empty(prenom),
        raison_sociale=_none_if_empty(raison_sociale),
        adresse=_none_if_empty(adresse),
        code_postal=_none_if_empty(code_postal),
        ville=_none_if_empty(ville),
        email=_none_if_empty(email),
        siret=_none_if_empty(siret),
        notes=_none_if_empty(notes),
    )
    session.add(client)
    session.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.get("/{client_id}/edit", response_class=HTMLResponse)
def edit_client_form(
    client_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    client = _get_client_for(client_id, user, session)
    return templates.TemplateResponse(
        request, "clients/_form.html", {"client": client},
    )


@router.post("/{client_id}/edit", response_class=HTMLResponse)
def edit_client(
    client_id: int,
    request: Request,
    type: str = Form(...),
    nom: str = Form(...),
    prenom: str = Form(""),
    raison_sociale: str = Form(""),
    adresse: str = Form(""),
    code_postal: str = Form(""),
    ville: str = Form(""),
    email: str = Form(""),
    siret: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    client = _get_client_for(client_id, user, session)
    client.type = type
    client.nom = nom.strip()
    client.prenom = _none_if_empty(prenom)
    client.raison_sociale = _none_if_empty(raison_sociale)
    client.adresse = _none_if_empty(adresse)
    client.code_postal = _none_if_empty(code_postal)
    client.ville = _none_if_empty(ville)
    client.email = _none_if_empty(email)
    client.siret = _none_if_empty(siret)
    client.notes = _none_if_empty(notes)
    session.add(client)
    session.commit()
    return RedirectResponse(url="/clients", status_code=303)
