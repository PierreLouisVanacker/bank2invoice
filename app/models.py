"""Modèles SQLModel.

Notes design :
- `Decimal` est sérialisé en TEXT par SQLite mais conserve sa précision via SQLModel.
- `factures` est conceptuellement append-only : pas de DELETE, pas d'UPDATE sur les
  champs comptables. Contrainte appliquée au niveau service.
- Multi-utilisateurs : chaque User a ses propres relevés, clients, factures et son
  profil. Toutes les requêtes métier filtrent par `user_id`.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


# ─── User ───────────────────────────────────────────────────────────────────

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str  # bcrypt
    nom_affichage: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True)


# ─── Releves ────────────────────────────────────────────────────────────────

class Releve(SQLModel, table=True):
    __tablename__ = "releves"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    nom_fichier: str
    banque: Optional[str] = None  # "CA" | "SG" | None si pas encore détecté
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
    # Le hash n'est plus unique globalement : unique par user (deux users peuvent
    # uploader le même PDF). L'unicité par user est vérifiée au niveau service.
    hash_fichier: str = Field(index=True)
    statut: str = Field(default="uploaded")  # uploaded | parse | erreur | quarantaine
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    parsed_at: Optional[datetime] = None


# ─── Clients ────────────────────────────────────────────────────────────────

class Client(SQLModel, table=True):
    __tablename__ = "clients"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    type: str  # "personne" | "societe"
    nom: str
    prenom: Optional[str] = None
    raison_sociale: Optional[str] = None
    adresse: Optional[str] = None
    code_postal: Optional[str] = None
    ville: Optional[str] = None
    siret: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ClientAlias(SQLModel, table=True):
    __tablename__ = "client_aliases"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id", index=True)
    alias_libelle: str = Field(index=True)
    poids: int = Field(default=1)


# ─── Transactions ───────────────────────────────────────────────────────────

class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    releve_id: int = Field(foreign_key="releves.id", index=True)
    date: date
    libelle_brut: str
    montant: Decimal
    sens: str  # "credit" | "debit"
    est_virement_entrant: bool = False

    # Extraction émetteur
    emetteur_type: Optional[str] = None  # "personne" | "societe" | "inconnu"
    emetteur_nom: Optional[str] = None
    emetteur_prenom: Optional[str] = None
    emetteur_civilite: Optional[str] = None
    emetteur_raison_sociale: Optional[str] = None

    # Matching client (proposition)
    client_id: Optional[int] = Field(default=None, foreign_key="clients.id")

    # Workflow review
    inclus: Optional[bool] = None  # None = pas reviewé, True/False = décision
    review_status: str = Field(default="a_valider")  # auto_ok | a_valider | valide | exclu

    # Date de facture choisie par l'utilisateur (override). Si None, on utilise
    # la date du virement (champ `date`) à la génération.
    date_facture_override: Optional[date] = None

    # Lien vers la facture générée, le cas échéant
    facture_id: Optional[int] = Field(default=None, foreign_key="factures.id")


# ─── Factures (append-only) ─────────────────────────────────────────────────

class Facture(SQLModel, table=True):
    __tablename__ = "factures"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # Numéro unique PAR USER (chaque user a sa propre séquence continue).
    # L'unicité (user_id, numero) est garantie au niveau service via la table
    # de séquence (Config). On garde un index pour la perf.
    numero: str = Field(index=True)
    client_id: int = Field(foreign_key="clients.id")
    transaction_id: int = Field(foreign_key="transactions.id")

    date_facture: date  # apparente sur le PDF, éditable, défaut = date virement
    date_emission: date  # date génération, non éditable, chronologique

    libelle_prestation: str
    montant_ttc: Decimal
    montant_ht: Decimal
    tva_taux: Decimal = Field(default=Decimal("0"))
    tva_montant: Decimal = Field(default=Decimal("0"))
    mention_legale_tva: str  # figée à la génération

    pdf_path: str
    status: str = Field(default="active")  # active | annulee_par_avoir
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Profil utilisateur (un par User) ──────────────────────────────────────

class ProfilUtilisateur(SQLModel, table=True):
    __tablename__ = "profil_utilisateur"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Lien 1:1 vers User. Chaque user a un et un seul profil.
    user_id: int = Field(foreign_key="users.id", unique=True, index=True)

    # Identité affichée
    nom: str
    nom_complet: str
    adresse: str
    code_postal: str
    ville: str
    pays: str = Field(default="France")

    # Mentions légales
    siret: str
    ape: Optional[str] = None
    numero_tva: Optional[str] = None

    # Contact
    email: str
    telephone: Optional[str] = None

    # Coordonnées bancaires (pour le pied de facture)
    iban: str
    bic: str
    code_banque: Optional[str] = None
    code_guichet: Optional[str] = None
    numero_compte: Optional[str] = None
    cle_rib: Optional[str] = None

    # TVA
    assujetti_tva: bool = False
    tva_taux_defaut: Optional[Decimal] = None
    mention_legale_non_assujetti: str = Field(
        default="TVA non applicable, art. 293 B du CGI"
    )

    # Mentions diverses
    mentions_pied_page: Optional[str] = None
    lieu_emission: str = Field(default="Paris")
    objet_defaut: str = Field(default="Facture pour prestations")
    designation_defaut: str = Field(default="Prestation")

    logo_path: Optional[str] = None


# ─── Config (clé/valeur) ────────────────────────────────────────────────────

class Config(SQLModel, table=True):
    """Clé/valeur globale. Pour les valeurs par-user (compteur factures),
    la clé inclut l'user_id (ex: `numero_compteur_2026_user42`)."""
    __tablename__ = "config"

    cle: str = Field(primary_key=True)
    valeur: str
