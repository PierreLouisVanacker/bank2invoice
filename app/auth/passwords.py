"""Hash et vérification de mots de passe avec bcrypt.

bcrypt est l'algorithme standard recommandé pour le stockage de mots de passe :
- coût (rounds) ajustable, lent par design pour résister au brute force
- sel intégré, pas besoin de gérer le sel séparément
- format du hash : `$2b$<cost>$<salt><hash>` (60 caractères ASCII)
"""

from __future__ import annotations

import bcrypt

# Coût bcrypt : 12 = ~250ms par hash sur un CPU moderne, bon compromis.
# Si trop lent pour ton hardware, baisser à 10. Ne pas descendre sous 10.
_BCRYPT_ROUNDS = 12


def hash_password(plaintext: str) -> str:
    """Renvoie le hash bcrypt d'un mot de passe en clair."""
    if not plaintext:
        raise ValueError("Mot de passe vide")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("ascii")


def verify_password(plaintext: str, hashed: str) -> bool:
    """Vérifie si plaintext correspond au hash bcrypt fourni.

    Renvoie False sur toute erreur (hash malformé, etc.) plutôt que de lever.
    """
    if not plaintext or not hashed:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
