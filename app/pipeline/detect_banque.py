"""Détection de la banque émettrice d'un relevé à partir de son texte.

Pour l'instant, deux banques supportées : Crédit Agricole et Société Générale.
On cherche des signatures texte distinctives dans les premières pages.
"""

# Marqueurs distinctifs par banque. On les cherche dans le texte du relevé
# (insensible à la casse).
_BANK_MARKERS: dict[str, tuple[str, ...]] = {
    "CA": (
        "CREDIT AGRICOLE",
        "CRÉDIT AGRICOLE",
        "AGRIFRPP",  # début BIC CA très distinctif
        "CA-CMDS",
        "Caisse Régionale de Crédit Agricole",
    ),
    "SG": (
        "SOCIETE GENERALE",
        "SOCIÉTÉ GÉNÉRALE",
        "SOGEFRPP",  # BIC SG
        "Société Générale",
    ),
}


def detect_banque(text: str) -> str | None:
    """Renvoie le code banque ('CA', 'SG') ou None si non identifiée.

    Stratégie simple : premier marqueur trouvé gagne. Si plusieurs banques
    matchent (peu probable), on retourne celle avec le plus de marqueurs.
    """
    text_upper = text.upper()
    scores: dict[str, int] = {}

    for bank_code, markers in _BANK_MARKERS.items():
        score = sum(1 for marker in markers if marker.upper() in text_upper)
        if score > 0:
            scores[bank_code] = score

    if not scores:
        return None

    # Retourne la banque avec le plus de matches
    return max(scores, key=scores.get)
