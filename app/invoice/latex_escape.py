"""Échappement des caractères spéciaux LaTeX.

À appliquer sur toute valeur dynamique injectée dans un template `.tex` :
nom de client, libellé de prestation, etc.

Sans ça, un client nommé "Müller & Cie" fait planter pdflatex sur le `&`.

Subtilité : certains remplacements (backslash, tilde, circonflexe) introduisent
eux-mêmes des ``\\``, ``{``, ``}`` dans leur résultat. Si on faisait des ``str.replace``
en chaîne, ces caractères seraient ré-échappés par les passes suivantes
(``\\textbackslash{}`` deviendrait ``\\textbackslash\\{\\}``).

Solution : remplacement en une seule passe par tokenisation caractère par
caractère. Plus lent en théorie, mais sur des chaînes de quelques mots c'est
imperceptible et c'est correct.
"""

_LATEX_MAP: dict[str, str] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value: object) -> str:
    """Échappe les caractères spéciaux LaTeX dans une valeur.

    Accepte n'importe quel objet : il est d'abord converti en str.
    None est converti en chaîne vide.
    """
    if value is None:
        return ""
    text = str(value)
    # Passe unique : on construit la sortie caractère par caractère, donc les
    # séquences introduites par les remplacements ne sont pas re-traitées.
    return "".join(_LATEX_MAP.get(ch, ch) for ch in text)
