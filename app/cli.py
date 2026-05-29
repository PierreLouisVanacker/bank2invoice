"""Commandes CLI.

Usage :
    python -m app.cli seed       # cree les tables + migrations legeres
    python -m app.cli reset      # DESTRUCTIF : supprime la base et la recrée
    python -m app.cli adduser    # cree un user en mode interactif
"""

import sys

from sqlmodel import Session

from app.config import settings
from app.db import engine, init_db
from app.models import ProfilUtilisateur, User


def seed() -> None:
    """Initialise la base. Avec multi-utilisateurs, plus de profil par defaut :
    chaque user s'inscrit via /register."""
    print("=> Initialisation de la base...")
    init_db()
    print(f"  DB : {settings.database_url}")
    print()
    print("[OK] Base prete. Pour creer un compte :")
    print("  - via l'UI : demarre l'app (uvicorn app.main:app --reload) et va sur /register")
    print("  - via CLI : python -m app.cli adduser")


def adduser() -> None:
    """Cree un user en interactif (utile pour les tests)."""
    import getpass
    from app.auth.passwords import hash_password

    init_db()

    print("Creation d'un nouvel utilisateur :")
    email = input("  Email : ").strip().lower()
    nom = input("  Nom d'affichage : ").strip()
    pwd = getpass.getpass("  Mot de passe (8+ char) : ")
    if len(pwd) < 8:
        print("[ERR] Mot de passe trop court.")
        sys.exit(1)
    pwd2 = getpass.getpass("  Confirmer : ")
    if pwd != pwd2:
        print("[ERR] Les mots de passe different.")
        sys.exit(1)

    with Session(engine) as session:
        from sqlmodel import select
        if session.exec(select(User).where(User.email == email)).first():
            print("[ERR] Un compte existe deja avec cet email.")
            sys.exit(1)

        user = User(email=email, password_hash=hash_password(pwd), nom_affichage=nom)
        session.add(user)
        session.commit()
        session.refresh(user)

        # Profil vide
        profil = ProfilUtilisateur(
            user_id=user.id,
            nom=nom, nom_complet=nom,
            adresse="", code_postal="", ville="",
            siret="", email=email,
            iban="", bic="",
            lieu_emission="Paris",
            objet_defaut="Facture pour prestations",
            designation_defaut="Prestation",
        )
        session.add(profil)
        session.commit()
        print(f"[OK] User cree : id={user.id}, email={email}")
        print("  Configure ton profil sur /profil apres login.")


def reset() -> None:
    """DESTRUCTIF : supprime la base et la recrée."""
    from pathlib import Path
    db_path = Path(settings.database_url.replace("sqlite:///", ""))
    if db_path.exists():
        print(f"=> Suppression de {db_path}")
        db_path.unlink()
    journal = db_path.with_suffix(db_path.suffix + "-journal")
    if journal.exists():
        journal.unlink()
    seed()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if cmd == "seed":
        seed()
    elif cmd == "adduser":
        adduser()
    elif cmd == "reset":
        reset()
    else:
        print(f"Commande inconnue : {cmd}. Utilisez 'seed', 'adduser' ou 'reset'.")
        sys.exit(1)
