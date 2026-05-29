"""Service de génération de factures.

À partir d'une transaction incluse + son client + le profil utilisateur :
  1. Construit le contexte LaTeX (échappé)
  2. Attribue un numéro continu
  3. Compile le PDF
  4. Crée l'enregistrement Facture (append-only)
  5. Lie la transaction à la facture

Garde-fous :
  - idempotence : si la transaction a déjà une facture, on skip
  - le statut TVA est figé dans la facture au moment de la génération
  - la date d'émission = aujourd'hui (numérotation chronologique) ;
    la date de facture affichée = date du virement (modifiable au lot 5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session

from app.config import settings
from app.invoice.latex_escape import latex_escape
from app.invoice.numerotation import attribuer_numero
from app.invoice.render import LatexCompileError, compile_facture
from app.models import Client, Facture, ProfilUtilisateur, Transaction, User

_MOIS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


@dataclass
class GenerationResult:
    transaction_id: int
    status: str  # 'created' | 'skipped_existing' | 'error'
    numero: str | None = None
    pdf_path: str | None = None
    errors: list[str] = field(default_factory=list)


def _date_fr(d: date) -> str:
    return f"{d.day} {_MOIS_FR[d.month]} {d.year}"


def _client_nom_complet(client: Client) -> str:
    if client.type == "personne":
        parts = [client.prenom or "", client.nom]
        return " ".join(p for p in parts if p).strip()
    return client.raison_sociale or client.nom


def _build_context(
    tx: Transaction,
    client: Client,
    profil: ProfilUtilisateur,
    numero: str,
    date_facture: date,
) -> dict:
    """Construit le contexte LaTeX, avec échappement de toutes les valeurs
    dynamiques (nom client, désignation, etc.)."""
    assujetti = profil.assujetti_tva
    tva_taux = (profil.tva_taux_defaut or Decimal("20")) if assujetti else Decimal("0")

    # Le montant du relevé est TTC. On décompose :
    #   - assujetti : HT = TTC / (1 + taux/100), TVA = TTC - HT
    #   - franchise : HT = TTC (pas de TVA)
    montant_ttc = tx.montant
    if assujetti and tva_taux > 0:
        montant_ht = (montant_ttc / (1 + tva_taux / 100)).quantize(Decimal("0.01"))
        montant_tva = montant_ttc - montant_ht
    else:
        montant_ht = montant_ttc
        montant_tva = Decimal("0")

    # Nom du client, avec adresse si disponible (tout échappé pour LaTeX)
    client_nom = _client_nom_complet(client)
    if client.adresse:
        adresse_parts = [client.adresse]
        if client.code_postal or client.ville:
            adresse_parts.append(f"{client.code_postal or ''} {client.ville or ''}".strip())
        client_bloc = latex_escape(client_nom) + r" \\ " + r" \\ ".join(
            latex_escape(p) for p in adresse_parts
        )
    else:
        client_bloc = latex_escape(client_nom)

    return {
        # Header
        "tva_taux": f"{tva_taux:.0f}" if tva_taux else "0",
        "facture_date_fr": _date_fr(date_facture),
        "assujetti_tva": assujetti,
        # Montants pré-calculés (le template n'a plus à recalculer)
        "montant_ht": f"{montant_ht:.2f}",
        "montant_tva": f"{montant_tva:.2f}",
        "montant_ttc": f"{montant_ttc:.2f}",
        # Main
        "numero_facture": latex_escape(numero),
        "facture_acquittee": "oui",  # le virement a été reçu, donc payée
        "lieu_emission": latex_escape(profil.lieu_emission),
        "objet": latex_escape(profil.objet_defaut),
        "client_nom": client_bloc,
        "lignes": [
            {
                "designation": latex_escape(profil.designation_defaut),
                # On affiche le HT sur la ligne (cohérent avec le total HT)
                "montant": f"{montant_ht:.2f}",
            }
        ],
        # Footer (profil)
        "profil_nom": latex_escape(profil.nom),
        "profil_adresse": latex_escape(profil.adresse),
        "profil_code_postal": latex_escape(profil.code_postal),
        "profil_ville": latex_escape(profil.ville),
        "profil_email": latex_escape(profil.email),
        "profil_telephone": latex_escape(profil.telephone or ""),
        "profil_siret": latex_escape(profil.siret),
        "profil_numero_tva": latex_escape(profil.numero_tva or ""),
        "profil_iban": latex_escape(profil.iban),
        "profil_bic": latex_escape(profil.bic),
        "profil_code_banque": latex_escape(profil.code_banque or ""),
        "profil_code_guichet": latex_escape(profil.code_guichet or ""),
        "profil_numero_compte": latex_escape(profil.numero_compte or ""),
        "profil_cle_rib": latex_escape(profil.cle_rib or ""),
        "profil_mentions_pied_page": latex_escape(profil.mentions_pied_page or ""),
        "mention_legale_non_assujetti": latex_escape(profil.mention_legale_non_assujetti),
    }


def _client_from_emetteur(tx: Transaction, user_id: int, session: Session) -> Client | None:
    """Crée un client à partir de l'émetteur extrait sur la transaction.

    Renvoie None si l'émetteur ne contient aucun nom exploitable.
    Le client créé est persisté (committé) pour avoir un id, et rattaché au user.
    """
    if tx.emetteur_type == "societe":
        nom = (tx.emetteur_raison_sociale or "").strip()
        if not nom:
            return None
        client = Client(user_id=user_id, type="societe", nom=nom, raison_sociale=nom)
    else:
        nom = (tx.emetteur_nom or "").strip()
        prenom = (tx.emetteur_prenom or "").strip() or None
        if not nom and prenom:
            nom = prenom
            prenom = None
        if not nom:
            return None
        client = Client(user_id=user_id, type="personne", nom=nom, prenom=prenom)

    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def generer_facture(
    tx: Transaction,
    session: Session,
    user: User,
    date_facture: date | None = None,
) -> GenerationResult:
    """Génère une facture pour une transaction du user donné.

    Idempotent par transaction. Vérifie que la transaction et le client
    appartiennent au user.
    """
    # Idempotence
    if tx.facture_id is not None:
        existing = session.get(Facture, tx.facture_id)
        return GenerationResult(
            transaction_id=tx.id,
            status="skipped_existing",
            numero=existing.numero if existing else None,
            pdf_path=existing.pdf_path if existing else None,
        )

    if not tx.inclus:
        return GenerationResult(
            transaction_id=tx.id, status="error",
            errors=["Transaction non incluse — coche-la avant de générer."],
        )

    # Client : soit déjà associé (vérifier ownership), soit créé à la volée.
    if tx.client_id is not None:
        client = session.get(Client, tx.client_id)
        if client is None or client.user_id != user.id:
            return GenerationResult(
                transaction_id=tx.id, status="error",
                errors=[f"Client #{tx.client_id} introuvable ou non autorisé."],
            )
    else:
        client = _client_from_emetteur(tx, user.id, session)
        if client is None:
            return GenerationResult(
                transaction_id=tx.id, status="error",
                errors=[
                    "Impossible de générer : ni client associé, ni émetteur "
                    "exploitable (renseigne au moins un nom dans les détails)."
                ],
            )
        tx.client_id = client.id
        session.add(tx)

    # Profil du user connecté (plus de singleton id=1)
    from sqlmodel import select as _select
    profil = session.exec(
        _select(ProfilUtilisateur).where(ProfilUtilisateur.user_id == user.id)
    ).first()
    if profil is None:
        return GenerationResult(
            transaction_id=tx.id, status="error",
            errors=["Profil utilisateur absent. Configure-le sur /profil."],
        )

    # Dates
    date_emission = date.today()
    date_fact = date_facture or tx.date_facture_override or tx.date

    # Numéro continu (par user)
    numero = attribuer_numero(session, user.id, date_emission)

    # Montants (franchise → HT = TTC, pas de TVA)
    assujetti = profil.assujetti_tva
    if assujetti:
        taux = profil.tva_taux_defaut or Decimal("20")
        montant_ht = (tx.montant / (1 + taux / 100)).quantize(Decimal("0.01"))
        tva_montant = tx.montant - montant_ht
        mention_tva = ""
    else:
        taux = Decimal("0")
        montant_ht = tx.montant
        tva_montant = Decimal("0")
        mention_tva = profil.mention_legale_non_assujetti

    # Compilation PDF — rangement par user pour isoler les fichiers
    context = _build_context(tx, client, profil, numero, date_fact)
    pdf_filename = f"{numero}_{_safe_filename(_client_nom_complet(client))}.pdf"
    pdf_path = Path(settings.factures_dir) / f"user_{user.id}" / str(date_emission.year) / pdf_filename

    try:
        compile_facture(context, pdf_path)
    except LatexCompileError as e:
        # On n'a pas committé le compteur → rollback pour ne pas brûler le numéro
        session.rollback()
        return GenerationResult(
            transaction_id=tx.id, status="error",
            errors=[f"Compilation LaTeX échouée : {e}"],
        )

    # Enregistrement Facture
    facture = Facture(
        user_id=user.id,
        numero=numero,
        client_id=client.id,
        transaction_id=tx.id,
        date_facture=date_fact,
        date_emission=date_emission,
        libelle_prestation=profil.designation_defaut,
        montant_ttc=tx.montant,
        montant_ht=montant_ht,
        tva_taux=taux,
        tva_montant=tva_montant,
        mention_legale_tva=mention_tva,
        pdf_path=str(pdf_path),
        status="active",
    )
    session.add(facture)
    session.commit()
    session.refresh(facture)

    # Lie la transaction
    tx.facture_id = facture.id
    session.add(tx)
    session.commit()

    return GenerationResult(
        transaction_id=tx.id,
        status="created",
        numero=numero,
        pdf_path=str(pdf_path),
    )


def _safe_filename(name: str) -> str:
    """Nettoie un nom pour en faire un nom de fichier sûr."""
    import re
    cleaned = re.sub(r"[^\w\-]", "_", name, flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "client"
