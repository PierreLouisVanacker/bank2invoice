"""Router des relevés : liste, détail/review, upload.

Multi-utilisateurs : toutes les routes exigent un user connecté et filtrent
par `user_id`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.auth.deps import current_user
from app.config import settings
from app.db import get_session
from app.llm.factory import get_llm_client
from app.models import Client, Releve, Transaction, User, Facture
from app.pipeline.ingest import ingest_pdf

router = APIRouter(prefix="/releves", tags=["releves"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_releve_for(releve_id: int, user: User, session: Session) -> Releve:
    """Récupère un relevé en vérifiant l'ownership. 404 si introuvable ou
    appartient à un autre user (404 plutôt que 403 pour ne pas révéler
    l'existence d'objets appartenant à d'autres)."""
    releve = session.get(Releve, releve_id)
    if releve is None or releve.user_id != user.id:
        raise HTTPException(status_code=404, detail="Relevé introuvable")
    return releve


@router.get("", response_class=HTMLResponse)
def list_releves(
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Liste des relevés du user connecté."""
    releves = session.exec(
        select(Releve)
        .where(Releve.user_id == user.id)
        .order_by(Releve.uploaded_at.desc())
    ).all()

    stats: dict[int, dict[str, int]] = {}
    for r in releves:
        txs = session.exec(
            select(Transaction).where(Transaction.releve_id == r.id)
        ).all()
        stats[r.id] = {
            "total": len(txs),
            "entrants": sum(1 for tx in txs if tx.est_virement_entrant),
        }

    return templates.TemplateResponse(
        request,
        "releves/list.html",
        {"releves": releves, "stats": stats, "user": user},
    )


@router.get("/{releve_id}", response_class=HTMLResponse)
def view_releve(
    releve_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Détail d'un relevé : table des transactions avec review."""
    releve = _get_releve_for(releve_id, user, session)

    transactions = session.exec(
        select(Transaction)
        .where(Transaction.releve_id == releve_id)
        .order_by(Transaction.date)
    ).all()

    # Les clients listés sont ceux du user connecté.
    clients = session.exec(
        select(Client).where(Client.user_id == user.id).order_by(Client.nom)
    ).all()

    stats = {
        "total": len(transactions),
        "entrants": sum(1 for tx in transactions if tx.est_virement_entrant),
        "inclus": sum(1 for tx in transactions if tx.inclus),
    }

    return templates.TemplateResponse(
        request,
        "releves/detail.html",
        {
            "releve": releve,
            "transactions": transactions,
            "clients": clients,
            "stats": stats,
            "user": user,
        },
    )


@router.post("/upload")
async def upload_releve(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Reçoit un PDF, le sauvegarde, l'ingère pour le user connecté."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Le fichier doit être un PDF.")

    # Sauvegarde dans data/pdfs/<user_id>/ pour isoler les fichiers par user
    settings.ensure_dirs()
    user_pdfs = Path(settings.pdfs_dir) / f"user_{user.id}"
    user_pdfs.mkdir(parents=True, exist_ok=True)
    dest = user_pdfs / file.filename
    counter = 1
    while dest.exists():
        stem = Path(file.filename).stem
        dest = user_pdfs / f"{stem}_{counter}.pdf"
        counter += 1

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    llm = None
    try:
        llm = get_llm_client()
    except Exception:
        llm = None

    ingest_pdf(dest, session, user_id=user.id, llm=llm)
    return RedirectResponse(url="/releves", status_code=303)


@router.delete("/{releve_id}", response_class=HTMLResponse)
def delete_releve(
    releve_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Supprime un relevé et toutes ses transactions, sauf si des factures
    sont générées (contrainte d'intégrité comptable)."""
    releve = _get_releve_for(releve_id, user, session)

    txs = session.exec(
        select(Transaction).where(Transaction.releve_id == releve_id)
    ).all()

    if any(tx.facture_id is not None for tx in txs):
        return HTMLResponse(
            '<tr class="error"><td colspan="5">Suppression impossible : ce relevé a des factures générées.</td></tr>',
            status_code=422,
        )

    for tx in txs:
        session.delete(tx)
    session.delete(releve)
    session.commit()

    return HTMLResponse("")
