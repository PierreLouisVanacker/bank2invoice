"""Dépendance FastAPI `current_user`.

À utiliser dans toute route qui nécessite un user connecté :

    @router.get(...)
    def view(user: User = Depends(current_user), ...):
        ...

Pour les routes optionnellement protégées (ex: pages publiques qui adaptent
leur affichage selon login), utiliser `optional_current_user`.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.auth.sessions import SESSION_COOKIE_NAME, decode_session
from app.db import get_session
from app.models import User


def optional_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User | None:
    """Renvoie l'user connecté, ou None si pas connecté.

    Lit le cookie de session, le valide, charge l'user en base.
    """
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    user_id = decode_session(cookie)
    if user_id is None:
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def current_user(
    user: User | None = Depends(optional_current_user),
) -> User:
    """Renvoie l'user connecté ou lève 401 avec redirect vers /login."""
    if user is None:
        # On lève une HTTPException qui sera transformée en redirect par
        # le handler global (voir main.py). Ainsi les navigateurs sont
        # redirigés vers /login, et les clients API reçoivent un 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
        )
    return user
