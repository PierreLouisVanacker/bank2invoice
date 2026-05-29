"""Sessions par cookie signé (itsdangerous).

Choix de design : pas de table `sessions` en base. Le cookie contient
directement l'`user_id`, signé avec une clé secrète. Avantages :
  - simple, pas de cleanup à gérer
  - stateless côté serveur
  - rapide (pas de query DB pour valider la session)

Inconvénient : on ne peut pas révoquer une session côté serveur (sauf en
changeant la clé secrète). Pour cet usage (2-3 personnes de confiance),
c'est acceptable. Pour révoquer un user, on désactive son compte
(`User.is_active = False`).

Durée par défaut : 30 jours, prolongée à chaque requête (sliding session).
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.config import settings

SESSION_COOKIE_NAME = "b2i_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 jours


def _signer() -> TimestampSigner:
    secret = settings.session_secret
    if not secret or len(secret) < 16:
        raise RuntimeError(
            "SESSION_SECRET doit être défini dans .env et faire au moins 16 caractères. "
            "Génère-en un avec : python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return TimestampSigner(secret, salt="b2i-session-v1")


def encode_session(user_id: int) -> str:
    """Sérialise et signe l'user_id pour stockage dans le cookie."""
    return _signer().sign(str(user_id).encode("utf-8")).decode("utf-8")


def decode_session(cookie_value: str) -> int | None:
    """Décode et valide le cookie. Renvoie l'user_id ou None si invalide/expiré."""
    if not cookie_value:
        return None
    try:
        raw = _signer().unsign(cookie_value, max_age=SESSION_MAX_AGE_SECONDS)
    except (SignatureExpired, BadSignature):
        return None
    try:
        return int(raw.decode("utf-8"))
    except (ValueError, AttributeError):
        return None
