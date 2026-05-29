"""Tests du parser Crédit Agricole (approche extract_tables)."""

from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest

from app.pipeline import parse_ca
from app.pipeline.detect_banque import detect_banque
from app.pipeline.validate import validate

PDF_REF = Path("data/pdfs/releve_ca_2026_01.pdf")


@pytest.fixture(scope="module")
def releve():
    if not PDF_REF.exists():
        pytest.skip(f"PDF de référence absent : {PDF_REF}")
    return parse_ca.parse(PDF_REF)


# ─── Détection de banque ────────────────────────────────────────────────────


def test_detect_banque_ca():
    if not PDF_REF.exists():
        pytest.skip(f"PDF de référence absent : {PDF_REF}")
    with pdfplumber.open(str(PDF_REF)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    assert detect_banque(text) == "CA"


def test_detect_banque_returns_none_on_unknown():
    assert detect_banque("Du texte sans aucun marqueur reconnaissable") is None


def test_detect_banque_sg():
    text = "RELEVE DE COMPTE - SOCIETE GENERALE - BIC SOGEFRPP"
    assert detect_banque(text) == "SG"


# ─── Métadonnées ────────────────────────────────────────────────────────────


def test_metadata_periode(releve):
    from datetime import date
    assert releve.metadata.date_fin == date(2026, 1, 31)
    assert releve.metadata.date_debut == date(2026, 1, 1)


def test_metadata_soldes(releve):
    assert releve.metadata.solde_initial == Decimal("7798.06")
    assert releve.metadata.solde_final == Decimal("7007.30")


def test_metadata_totaux(releve):
    assert releve.metadata.total_debits == Decimal("6751.82")
    assert releve.metadata.total_credits == Decimal("5961.06")


# ─── Transactions ───────────────────────────────────────────────────────────


def test_nombre_transactions(releve):
    """Le relevé de janvier 2026 contient exactement 26 opérations."""
    assert len(releve.transactions) == 26


def test_transaction_keter_conseil(releve):
    """Première transaction : Keter Conseil, 1000 euros au crédit le 02/01."""
    from datetime import date
    tx = releve.transactions[0]
    assert tx.date_operation == date(2026, 1, 2)
    assert tx.montant == Decimal("1000.00")
    assert tx.sens == "credit"
    assert "Keter Conseil" in tx.libelle


def test_transaction_avec_milliers(releve):
    """Vérifie que les montants ≥1000€ sont bien extraits (pas tronqués)."""
    carpa_07 = next(
        tx for tx in releve.transactions
        if "Carpa" in tx.libelle and tx.date_operation.day == 7
    )
    assert carpa_07.montant == Decimal("2851.20")
    assert carpa_07.sens == "credit"


def test_transaction_multiligne_fusionnee(releve):
    """Les lignes filles (sans date) sont fusionnées dans le libellé."""
    urssaf = next(tx for tx in releve.transactions if "URSSAF" in tx.libelle)
    assert "117000001556860508" in urssaf.libelle


def test_aucun_artefact_visuel_dans_libelles(releve):
    """Les cases à cocher ¨ et þ ne doivent pas se retrouver dans les libellés."""
    for tx in releve.transactions:
        assert "¨" not in tx.libelle, f"Artefact ¨ dans : {tx.libelle!r}"
        assert "þ" not in tx.libelle, f"Artefact þ dans : {tx.libelle!r}"


def test_distinction_debit_credit(releve):
    """Spot checks de sens débit/crédit."""
    carpa = next(tx for tx in releve.transactions if "Carpa" in tx.libelle)
    assert carpa.sens == "credit"

    urssaf = next(tx for tx in releve.transactions if "URSSAF" in tx.libelle)
    assert urssaf.sens == "debit"

    loyer = next(tx for tx in releve.transactions if "loyer" in tx.libelle.lower())
    assert loyer.sens == "debit"
    assert loyer.montant == Decimal("1231.20")


def test_lignes_totaux_filtrees(releve):
    """Les lignes 'Total des opérations' et 'Nouveau solde' ne doivent PAS être
    présentes parmi les transactions extraites."""
    for tx in releve.transactions:
        assert not tx.libelle.startswith("Total des opérations")
        assert not tx.libelle.startswith("Nouveau solde")
        assert not tx.libelle.startswith("Ancien solde")


# ─── Validations ────────────────────────────────────────────────────────────


def test_validations_passent(releve):
    v = validate(releve)
    assert v.ok, f"Erreurs : {v.errors}"


def test_validation_somme_debits(releve):
    v = validate(releve)
    assert v.computed_total_debits == Decimal("6751.82")


def test_validation_somme_credits(releve):
    v = validate(releve)
    assert v.computed_total_credits == Decimal("5961.06")


def test_validation_solde_final_recalcule(releve):
    v = validate(releve)
    assert v.computed_solde_final == Decimal("7007.30")
