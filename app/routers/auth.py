"""Router d'authentification : login, register, logout.

Pages publiques (pas de auth requise pour y accéder) :
  - GET  /login    formulaire de connexion
  - POST /login    valide les credentials, pose le cookie de session
  - GET  /register formulaire d'inscription
  - POST /register crée le user (+ profil vide), pose le cookie, redirige
  - POST /logout   efface le cookie
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, encode_session
from app.db import get_session
from app.models import ProfilUtilisateur, User

router = APIRouter(tags=["auth"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _set_session_cookie(response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        encode_session(user_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # secure=True doit être activé en prod HTTPS. En dev local on laisse False.
    )


# ─── Login ──────────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    email_n = email.strip().lower()
    user = session.exec(select(User).where(User.email == email_n)).first()

    if user is None or not verify_password(password, user.password_hash):
        # Message volontairement vague (ne pas révéler si l'email existe)
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"error": "Email ou mot de passe incorrect.", "email": email_n},
            status_code=401,
        )

    if not user.is_active:
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"error": "Ce compte est désactivé.", "email": email_n},
            status_code=403,
        )

    response = RedirectResponse(url="/releves", status_code=303)
    _set_session_cookie(response, user.id)
    return response


# ─── Register ───────────────────────────────────────────────────────────────


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth/register.html", {"error": None})


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    nom_affichage: str = Form(...),
    session: Session = Depends(get_session),
):
    email_n = email.strip().lower()
    nom = nom_affichage.strip()

    # Validations
    errors = []
    if not EMAIL_RE.match(email_n):
        errors.append("Email invalide.")
    if len(password) < 8:
        errors.append("Le mot de passe doit faire au moins 8 caractères.")
    if password != password_confirm:
        errors.append("Les deux mots de passe ne correspondent pas.")
    if not nom:
        errors.append("Renseigne ton nom d'affichage.")

    if not errors:
        existing = session.exec(select(User).where(User.email == email_n)).first()
        if existing is not None:
            errors.append("Un compte existe déjà avec cet email.")

    if errors:
        return templates.TemplateResponse(
            request, "auth/register.html",
            {"error": " ".join(errors), "email": email_n, "nom_affichage": nom},
            status_code=400,
        )

    # Création
    user = User(
        email=email_n,
        password_hash=hash_password(password),
        nom_affichage=nom,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    # Création d'un profil vide pour ce user (sera complété sur /profil)
    profil = ProfilUtilisateur(
        user_id=user.id,
        nom=nom, nom_complet=nom,
        adresse="", code_postal="", ville="",
        siret="", email=email_n,
        iban="", bic="",
        lieu_emission="Paris",
        objet_defaut="Facture pour prestations",
        designation_defaut="Prestation",
    )
    session.add(profil)
    session.commit()

    response = RedirectResponse(url="/profil", status_code=303)
    _set_session_cookie(response, user.id)
    return response


# ─── Logout ─────────────────────────────────────────────────────────────────


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
