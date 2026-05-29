"""Tests Lot 2 : filtrage, extraction émetteur, matching, ingestion bout-en-bout."""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.llm.stub import StubLLM
from app.models import Client, ClientAlias, Releve, Transaction
from app.pipeline.emetteur import extract_emetteur, preprocess_libelle
from app.pipeline.filter_entrants import filter_transaction
from app.pipeline.ingest import ingest_pdf
from app.pipeline.match_client import find_client_for_transaction

PDF_REF = Path("data/pdfs/releve_ca_2026_01.pdf")


# ─── Fixture : DB en mémoire ────────────────────────────────────────────────


@pytest.fixture
def session():
    """SQLite en mémoire pour chaque test, propre."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ─── filter_entrants ────────────────────────────────────────────────────────


def test_filter_virement_personne_a_valider():
    d = filter_transaction("Virement Vir Inst de Mme Khelili Houda", "credit")
    assert d.est_virement_entrant is True
    assert d.review_status == "a_valider"


def test_filter_virement_societe_a_valider():
    d = filter_transaction("Virement Vir Inst de Keter Conseil", "credit")
    assert d.est_virement_entrant is True
    assert d.review_status == "a_valider"


def test_filter_carpa_a_valider():
    d = filter_transaction("Virement Carpa / 118071", "credit")
    assert d.est_virement_entrant is True
    assert d.review_status == "a_valider"
    assert "CARPA" in d.raison


def test_filter_debit_exclu():
    d = filter_transaction("Virement Vir Inst de Keter Conseil", "debit")
    assert d.est_virement_entrant is False
    assert d.review_status == "exclu"


def test_filter_auto_virement_exclu():
    d = filter_transaction("Virement Vir Inst vers compte fortuneo", "credit")
    assert d.est_virement_entrant is False
    assert d.review_status == "exclu"
    assert "Fortuneo" in d.raison


def test_filter_remboursement_exclu():
    d = filter_transaction("Regul Remboursement cotisation carte", "credit")
    assert d.est_virement_entrant is False
    assert d.review_status == "exclu"


def test_filter_credit_non_virement_exclu():
    """Un crédit qui n'est pas un virement (remise de chèque par ex) → exclu."""
    d = filter_transaction("Rem Chq 2795520", "credit")
    assert d.est_virement_entrant is False


# ─── preprocess_libelle ─────────────────────────────────────────────────────


def test_preprocess_retire_refs_alphanumeriques():
    libelle = "Virement Vir Inst de Mme X / 168a4adbed4a11f0a439bf065156b2fc"
    cleaned = preprocess_libelle(libelle)
    assert "168a4adbed4a11f0" not in cleaned


def test_preprocess_garde_civilite_dans_reference():
    """Cas Noémie Le Duc : la civilité est dans la 2e partie."""
    libelle = (
        "Virement Noemie Desire Le Duc / "
        "168a4adbed4a11f0a439bf065156b2fcMlle le duc Noemie"
    )
    cleaned = preprocess_libelle(libelle)
    # Le hash doit avoir disparu, mais la 2e partie reste car contient "Mlle"
    assert "168a4adbed" not in cleaned
    assert "Mlle" in cleaned


# ─── extract_emetteur avec StubLLM ──────────────────────────────────────────


def test_extract_emetteur_stub_personne():
    em = extract_emetteur("Virement Vir Inst de Mme Khelili Houda", StubLLM())
    assert em.type == "personne"
    assert em.civilite == "Mme"


def test_extract_emetteur_stub_societe_avec_marqueur():
    em = extract_emetteur("Virement Vir Inst de Keter Conseil SARL", StubLLM())
    assert em.type == "societe"
    assert "Keter Conseil" in (em.raison_sociale or "")


def test_extract_emetteur_libelle_vide():
    em = extract_emetteur("", StubLLM())
    assert em.type == "inconnu"


# ─── match_client ───────────────────────────────────────────────────────────


def test_match_par_alias(session):
    # Crée un client + un alias
    client = Client(type="personne", nom="Dupont", prenom="Jean")
    session.add(client)
    session.commit()
    session.refresh(client)
    session.add(ClientAlias(client_id=client.id, alias_libelle="Jean Dupont", poids=5))
    session.commit()

    from app.llm.base import EmetteurExtrait

    em = EmetteurExtrait(type="personne", nom="Autre", prenom="Truc")  # ne devrait pas être utilisé

    # Le libellé contient l'alias → match par alias
    match_id = find_client_for_transaction(
        "Virement Vir Inst de Jean Dupont", em, session
    )
    assert match_id == client.id


def test_match_par_nom_prenom(session):
    client = Client(type="personne", nom="Khelili", prenom="Houda")
    session.add(client)
    session.commit()
    session.refresh(client)

    from app.llm.base import EmetteurExtrait

    em = EmetteurExtrait(type="personne", nom="Khelili", prenom="Houda")
    match_id = find_client_for_transaction(
        "Virement Vir Inst de Mme Khelili Houda", em, session
    )
    assert match_id == client.id


def test_match_par_raison_sociale(session):
    client = Client(type="societe", nom="Keter Conseil", raison_sociale="Keter Conseil")
    session.add(client)
    session.commit()
    session.refresh(client)

    from app.llm.base import EmetteurExtrait

    em = EmetteurExtrait(type="societe", raison_sociale="Keter Conseil")
    match_id = find_client_for_transaction(
        "Virement Vir Inst de Keter Conseil", em, session
    )
    assert match_id == client.id


def test_match_pas_de_match(session):
    from app.llm.base import EmetteurExtrait

    em = EmetteurExtrait(type="personne", nom="Inexistant", prenom="Personne")
    match_id = find_client_for_transaction("libelle quelconque", em, session)
    assert match_id is None


# ─── Ingestion bout-en-bout ─────────────────────────────────────────────────


@pytest.fixture
def releve_ingere(session):
    """Ingère le PDF de référence (sans LLM pour rester reproductible)."""
    if not PDF_REF.exists():
        pytest.skip(f"PDF de référence absent : {PDF_REF}")
    result = ingest_pdf(PDF_REF, session, llm=None)
    return result


def test_ingest_status_created(releve_ingere):
    assert releve_ingere.status == "created"
    assert releve_ingere.banque == "CA"
    assert releve_ingere.nb_transactions == 26


def test_ingest_idempotent(session):
    """Réingérer le même PDF → status='already_present', pas de doublons."""
    if not PDF_REF.exists():
        pytest.skip(f"PDF de référence absent : {PDF_REF}")

    r1 = ingest_pdf(PDF_REF, session, llm=None)
    assert r1.status == "created"

    r2 = ingest_pdf(PDF_REF, session, llm=None)
    assert r2.status == "already_present"
    assert r2.releve_id == r1.releve_id

    # On a bien UN seul relevé en base
    releves = session.exec(select(Releve)).all()
    assert len(releves) == 1


def test_ingest_filtrage_correct(session, releve_ingere):
    """Sur le relevé janvier 2026, on attend 7 virements entrants candidats."""
    entrants = session.exec(
        select(Transaction).where(Transaction.est_virement_entrant == True)  # noqa: E712
    ).all()
    # Keter, Benmeziane, Carpa×2, Le Duc, Abdelghani, Khelili = 7
    assert len(entrants) == 7


def test_ingest_exclus_correct(session, releve_ingere):
    """Le virement 'vers compte fortuneo' doit être exclu."""
    fortuneo = session.exec(
        select(Transaction).where(Transaction.libelle_brut.contains("fortuneo"))
    ).first()
    assert fortuneo is not None
    assert fortuneo.est_virement_entrant is False


def test_ingest_carpa_a_valider(session, releve_ingere):
    """Les virements CARPA sont inclus mais en review_status='a_valider'."""
    carpas = session.exec(
        select(Transaction).where(Transaction.libelle_brut.contains("Carpa"))
    ).all()
    assert len(carpas) == 2
    for c in carpas:
        assert c.est_virement_entrant is True
        assert c.review_status == "a_valider"


def test_ingest_avec_stub_llm_remplit_emetteur(session):
    """Avec un LLM (même le stub), les champs emetteur_* sont remplis pour les
    virements entrants candidats."""
    if not PDF_REF.exists():
        pytest.skip(f"PDF de référence absent : {PDF_REF}")

    ingest_pdf(PDF_REF, session, llm=StubLLM())

    entrants = session.exec(
        select(Transaction).where(Transaction.est_virement_entrant == True)  # noqa: E712
    ).all()
    assert len(entrants) == 7

    # Au moins quelques-uns doivent avoir un type extrait (le stub est limité
    # mais il doit reconnaître les personnes avec civilité au moins)
    typed = [tx for tx in entrants if tx.emetteur_type in ("personne", "societe")]
    assert len(typed) >= 3, f"Trop peu d'extractions stub réussies : {len(typed)}"


def test_ingest_montants_preserves_precision(session, releve_ingere):
    """La précision Decimal est préservée à travers SQLite."""
    keter = session.exec(
        select(Transaction).where(Transaction.libelle_brut.contains("Keter"))
    ).first()
    assert keter.montant == Decimal("1000.00")
