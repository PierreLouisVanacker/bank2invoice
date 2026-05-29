"""Tests du module invoice : échappement LaTeX et rendu de templates."""

from app.invoice.latex_escape import latex_escape
from app.invoice.render import render_template


# ─── latex_escape ───────────────────────────────────────────────────────────


def test_escape_ampersand():
    assert latex_escape("Müller & Cie") == r"Müller \& Cie"


def test_escape_underscore():
    assert latex_escape("client_dupont") == r"client\_dupont"


def test_escape_percent_and_dollar():
    assert latex_escape("50% des $100") == r"50\% des \$100"


def test_escape_backslash_first():
    """Le backslash doit être traité avant les autres (sinon ré-échappage)."""
    result = latex_escape("a\\b")
    assert result == r"a\textbackslash{}b"


def test_escape_none_returns_empty():
    assert latex_escape(None) == ""


def test_escape_number():
    assert latex_escape(42) == "42"


def test_escape_braces():
    assert latex_escape("{x}") == r"\{x\}"


def test_escape_hash():
    assert latex_escape("#tag") == r"\#tag"


# ─── render_template ────────────────────────────────────────────────────────


def _minimal_context(**overrides) -> dict:
    """Contexte minimal pour rendre un template facture complet."""
    ctx = {
        # Header
        "tva_taux": "0",
        "facture_date_fr": "22 mai 2026",
        "assujetti_tva": False,
        # Main
        "numero_facture": "2026-0001",
        "facture_acquittee": "non",
        "lieu_emission": "Paris",
        "objet": "Facture pour prestations juridiques",
        "client_nom": "M. Jean Dupont",
        "lignes": [{"designation": "Assistance juridique", "montant": "1200"}],
        # Footer (profil)
        "profil_nom": "Lorène CARDOT Avocat",
        "profil_adresse": "5 avenue Alphand",
        "profil_code_postal": "75116",
        "profil_ville": "Paris",
        "profil_email": "l.cardot.avocat@gmail.com",
        "profil_telephone": "+33 (0)6 61 11 57 29",
        "profil_siret": "835 144 924",
        "profil_numero_tva": "FR56 835 144 924",
        "profil_iban": "FR76 1170 6000 0856 0389 7804 284",
        "profil_bic": "AGRIFRPP817",
        "profil_code_banque": "11706",
        "profil_code_guichet": "00008",
        "profil_numero_compte": "56038978042",
        "profil_cle_rib": "84",
        "profil_mentions_pied_page": (
            "Dispensé d'immatriculation au registre du commerce et des sociétés "
            "et au répertoire des métiers"
        ),
        "mention_legale_non_assujetti": "TVA non applicable, art. 293 B du CGI",
    }
    ctx.update(overrides)
    return ctx


def test_render_header_with_franchise():
    """En franchise TVA, le template doit afficher juste 'Total', pas 'Total HT/TVA/TTC'."""
    ctx = _minimal_context(assujetti_tva=False)
    out = render_template("facture_header.tex.j2", ctx)
    assert r"\textbf{Total}" in out
    assert "Total HT" not in out
    assert "TVA" not in out or r"\def\TVA" in out  # \def\TVA est le seul TVA toléré


def test_render_header_with_tva():
    """Assujetti : ligne HT, TVA et TTC apparaissent."""
    ctx = _minimal_context(assujetti_tva=True, tva_taux="20")
    out = render_template("facture_header.tex.j2", ctx)
    assert "Total HT" in out
    assert r"TVA \TVA~\%" in out
    assert "Total TTC" in out


def test_render_main_with_multiple_lignes():
    ctx = _minimal_context(
        lignes=[
            {"designation": "Consultation initiale", "montant": "300"},
            {"designation": "Rédaction acte", "montant": "900"},
        ],
    )
    out = render_template("facture_main.tex.j2", ctx)
    assert r"\AjouterLigne{Consultation initiale}{300}" in out
    assert r"\AjouterLigne{Rédaction acte}{900}" in out


def test_render_footer_franchise_displays_mention():
    """Le footer doit imprimer la mention 'TVA non applicable' si non assujetti."""
    ctx = _minimal_context(assujetti_tva=False)
    out = render_template("facture_footer.tex.j2", ctx)
    assert "TVA non applicable, art. 293 B du CGI" in out


def test_render_footer_assujetti_skips_mention():
    ctx = _minimal_context(assujetti_tva=True)
    out = render_template("facture_footer.tex.j2", ctx)
    assert "TVA non applicable" not in out


def test_render_footer_contains_iban():
    ctx = _minimal_context()
    out = render_template("facture_footer.tex.j2", ctx)
    assert "FR76 1170 6000 0856 0389 7804 284" in out
    assert "AGRIFRPP817" in out
