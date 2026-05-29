"""Router pour la page profil utilisateur.

Affichage + édition du profil du user connecté.
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.auth.deps import current_user
from app.db import get_session
from app.models import ProfilUtilisateur, User

router = APIRouter(prefix="/profil", tags=["profil"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_profil_for(user: User, session: Session) -> ProfilUtilisateur:
    profil = session.exec(
        select(ProfilUtilisateur).where(ProfilUtilisateur.user_id == user.id)
    ).first()
    if profil is None:
        raise HTTPException(status_code=404, detail="Profil introuvable.")
    return profil


def _none_if_empty(s: str) -> str | None:
    s = (s or "").strip()
    return s if s else None


@router.get("", response_class=HTMLResponse)
def view_profil(
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Page complète du profil (formulaire éditable)."""
    profil = _get_profil_for(user, session)
    return templates.TemplateResponse(
        request,
        "profil/view.html",
        {"profil": profil, "user": user},
    )


@router.post("", response_class=HTMLResponse)
def save_profil(
    request: Request,
    nom: str = Form(...),
    nom_complet: str = Form(...),
    adresse: str = Form(...),
    code_postal: str = Form(...),
    ville: str = Form(...),
    pays: str = Form("France"),
    siret: str = Form(...),
    numero_tva: str = Form(""),
    email: str = Form(...),
    telephone: str = Form(""),
    iban: str = Form(...),
    bic: str = Form(...),
    code_banque: str = Form(""),
    code_guichet: str = Form(""),
    numero_compte: str = Form(""),
    cle_rib: str = Form(""),
    assujetti_tva: bool = Form(False),
    tva_taux_defaut: str = Form(""),
    mention_legale_non_assujetti: str = Form(""),
    mentions_pied_page: str = Form(""),
    lieu_emission: str = Form("Paris"),
    objet_defaut: str = Form(""),
    designation_defaut: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Sauvegarde tout le profil de l'utilisateur connecté."""
    profil = _get_profil_for(user, session)

    profil.nom = nom.strip()
    profil.nom_complet = nom_complet.strip()
    profil.adresse = adresse.strip()
    profil.code_postal = code_postal.strip()
    profil.ville = ville.strip()
    profil.pays = pays.strip() or "France"
    profil.siret = siret.strip()
    profil.numero_tva = _none_if_empty(numero_tva)
    profil.email = email.strip()
    profil.telephone = _none_if_empty(telephone)
    profil.iban = iban.strip()
    profil.bic = bic.strip()
    profil.code_banque = _none_if_empty(code_banque)
    profil.code_guichet = _none_if_empty(code_guichet)
    profil.numero_compte = _none_if_empty(numero_compte)
    profil.cle_rib = _none_if_empty(cle_rib)
    profil.assujetti_tva = assujetti_tva

    taux = _none_if_empty(tva_taux_defaut)
    if taux:
        try:
            profil.tva_taux_defaut = Decimal(taux.replace(",", "."))
        except InvalidOperation:
            pass
    else:
        profil.tva_taux_defaut = None

    if _none_if_empty(mention_legale_non_assujetti):
        profil.mention_legale_non_assujetti = mention_legale_non_assujetti.strip()
    profil.mentions_pied_page = _none_if_empty(mentions_pied_page)
    profil.lieu_emission = lieu_emission.strip() or "Paris"
    if _none_if_empty(objet_defaut):
        profil.objet_defaut = objet_defaut.strip()
    if _none_if_empty(designation_defaut):
        profil.designation_defaut = designation_defaut.strip()

    session.add(profil)
    session.commit()

    return RedirectResponse(url="/profil", status_code=303)


@router.post("/tva", response_class=HTMLResponse)
def toggle_tva(
    request: Request,
    assujetti_tva: bool = Form(False),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Endpoint HTMX : toggle le statut TVA (raccourci depuis le badge)."""
    profil = _get_profil_for(user, session)
    profil.assujetti_tva = assujetti_tva
    session.add(profil)
    session.commit()
    session.refresh(profil)

    return templates.TemplateResponse(
        request,
        "profil/_tva_badge.html",
        {"profil": profil},
    )
