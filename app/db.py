"""Setup de la base SQLite via SQLModel + migrations légères."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,  # mis à False : trop de bruit dans la console
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Crée les tables manquantes + applique les migrations légères."""
    from app import models  # noqa: F401

    settings.ensure_dirs()
    SQLModel.metadata.create_all(engine)
    _apply_light_migrations()


def _apply_light_migrations() -> None:
    """Migrations légères pour les bases créées avant l'ajout de colonnes.

    Ajout par ALTER TABLE idempotent. Pour les migrations qui doivent
    rattacher des données existantes à un user, on crée un user "legacy"
    et on lui attribue toutes les lignes orphelines.
    """
    from sqlalchemy import text
    from sqlmodel import Session, select
    from app.models import User
    from app.auth.passwords import hash_password

    # 1. Ajout simple de colonnes (idempotent)
    expected_columns = [
        ("transactions", "date_facture_override", "DATE"),
        ("releves",      "user_id",              "INTEGER"),
        ("clients",      "user_id",              "INTEGER"),
        ("factures",     "user_id",              "INTEGER"),
        ("profil_utilisateur", "user_id",        "INTEGER"),
    ]

    with engine.connect() as conn:
        for table, column, sql_type in expected_columns:
            try:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            except Exception:
                # Table inexistante (nouvelle base) — create_all l'a déjà créée
                # avec les bonnes colonnes, on skip
                continue
            existing = {row[1] for row in rows}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                conn.commit()

    # 2. Rattachement des données legacy : si des lignes ont user_id NULL,
    # on les rattache à un user "legacy" (premier user, ou en crée un).
    with Session(engine) as session:
        # Y a-t-il des lignes orphelines ?
        orphans = session.execute(text(
            "SELECT COUNT(*) FROM releves WHERE user_id IS NULL"
        )).scalar() or 0
        orphans += session.execute(text(
            "SELECT COUNT(*) FROM clients WHERE user_id IS NULL"
        )).scalar() or 0
        orphans += session.execute(text(
            "SELECT COUNT(*) FROM factures WHERE user_id IS NULL"
        )).scalar() or 0
        orphans += session.execute(text(
            "SELECT COUNT(*) FROM profil_utilisateur WHERE user_id IS NULL"
        )).scalar() or 0

        if orphans == 0:
            return

        # Récupère ou crée le user legacy
        legacy = session.exec(select(User).order_by(User.id)).first()
        if legacy is None:
            # Mot de passe temporaire affiché dans la console
            import secrets
            temp_pwd = secrets.token_urlsafe(12)
            legacy = User(
                email="legacy@bank2invoice.local",
                password_hash=hash_password(temp_pwd),
                nom_affichage="Utilisateur initial",
            )
            session.add(legacy)
            session.commit()
            session.refresh(legacy)
            print()
            print("=" * 60)
            print("MIGRATION : un compte 'legacy' a été créé pour reprendre")
            print("tes données existantes.")
            print(f"  Email    : legacy@bank2invoice.local")
            print(f"  Mot de passe (à changer après login) : {temp_pwd}")
            print("=" * 60)
            print()

        # Rattache toutes les lignes orphelines à ce user
        for table in ("releves", "clients", "factures", "profil_utilisateur"):
            session.execute(text(
                f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"
            ), {"uid": legacy.id})
        session.commit()


def get_session() -> Generator[Session, None, None]:
    """Dépendance FastAPI : une session par requête."""
    with Session(engine) as session:
        yield session
