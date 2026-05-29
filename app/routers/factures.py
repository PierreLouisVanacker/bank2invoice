"""Router factures : génération, liste, téléchargement PDF.

Multi-utilisateurs : chaque user ne voit que ses factures.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.auth.deps import current_user
from app.db import get_session
from app.invoice.generate import generer_facture
from app.models import Client, Facture, Releve, Transaction, User

router = APIRouter(prefix="/factures", tags=["factures"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ensure_releve_ownership(releve_id: int, user: User, session: Session) -> Releve:
    releve = session.get(Releve, releve_id)
    if releve is None or releve.user_id != user.id:
        raise HTTPException(status_code=404, detail="Relevé introuvable")
    return releve


@router.post("/generate/{releve_id}", response_class=HTMLResponse)
def generate_for_releve(
    releve_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Génère les factures pour toutes les transactions incluses du relevé."""
    _ensure_releve_ownership(releve_id, user, session)

    transactions = session.exec(
        select(Transaction).where(
            Transaction.releve_id == releve_id,
            Transaction.inclus == True,  # noqa: E712
        )
    ).all()

    results = [generer_facture(tx, session, user=user) for tx in transactions]

    return templates.TemplateResponse(
        request,
        "factures/_generation_result.html",
        {
            "created": [r for r in results if r.status == "created"],
            "skipped": [r for r in results if r.status == "skipped_existing"],
            "errors": [r for r in results if r.status == "error"],
            "total": len(results),
        },
    )


@router.get("", response_class=HTMLResponse)
def list_factures(
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Liste des factures du user connecté."""
    factures = session.exec(
        select(Facture).where(Facture.user_id == user.id).order_by(Facture.numero.desc())
    ).all()
    clients = {
        c.id: c for c in session.exec(
            select(Client).where(Client.user_id == user.id)
        ).all()
    }
    return templates.TemplateResponse(
        request, "factures/list.html",
        {"factures": factures, "clients": clients, "user": user},
    )


@router.get("/{facture_id}/pdf")
def download_pdf(
    facture_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    facture = session.get(Facture, facture_id)
    if facture is None or facture.user_id != user.id:
        raise HTTPException(status_code=404, detail="Facture introuvable")

    pdf_path = Path(facture.pdf_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF absent : {pdf_path}")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"facture_{facture.numero}.pdf",
    )
