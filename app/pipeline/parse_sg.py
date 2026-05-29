"""Parser de relevé Société Générale.

Stratégie :
- Extraction text-based pour la structure des transactions
  (chaque opération débute par une ligne DD/MM/YYYY DD/MM/YYYY …)
- Colonnes débit/crédit de la table pdfplumber pour distinguer le sens
  (matching séquentiel : liste ordonnée débits, liste ordonnée crédits)
- Gestion des PDFs multi-mois : le fichier peut contenir plusieurs mois
  (Jan–Jul par exemple) voire des doublons (copie client + copie banque).
  Les doublons sont détectés par le marqueur "Page 1/N" combiné à la période.

Particularités SG vs CA :
- Dates au format DD/MM/YYYY (vs DD.MM pour CA)
- Milliers séparés par le point (vs espace pour CA)
- Toutes les transactions d'une page packed dans UNE seule ligne de table,
  les montants débit et crédit dans des colonnes séparées (col 3 et 4)
- Le fichier peut couvrir plusieurs mois — validation agrège tous les totaux
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

# Réutilise les types de sortie partagés définis dans parse_ca
from app.pipeline.parse_ca import ParsedReleve, ParsedTransaction, ReleveMetadata

# ─── Expressions régulières ──────────────────────────────────────────────────

# Ligne de transaction : DD/MM/YYYY DD/MM/YYYY REST
_TX_LINE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.+)$")

# Montant SG : 1 à 3 chiffres, groupes de 3 séparés par ".", décimale ","
# Lookbehind/lookahead négatifs pour éviter les faux positifs dans les références.
_AMOUNT_RE = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{3})*,\d{2}\*?(?!\d)")

# Détection du début d'un nouveau groupe mensuel : "Page 1/N" dans l'en-tête
_PAGE1_RE = re.compile(r"Page\s*1\s*/\s*\d+", re.IGNORECASE)

# Métadonnées textuelles (mots fusionnés car pdfplumber retire les espaces)
_PERIOD_RE = re.compile(
    r"du\s*(\d{2}/\d{2}/\d{4})\s*au\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE
)
_SOLDE_PREC_RE = re.compile(
    r"SOLDE.{0,20}DENT\s*AU\s*(\d{2}/\d{2}/\d{4})\s+([\d.\s]+,\d{2})",
    re.IGNORECASE,
)
_NOUVEAU_SOLDE_RE = re.compile(
    r"NOUVEAU\s*SOLDE\s*AU\s*(\d{2}/\d{2}/\d{4})\s*[+-]?([\d.\s]+,\d{2})",
    re.IGNORECASE,
)
_TOTAUX_RE = re.compile(
    r"TOTAUX\s*DES\s*MOUVEMENTS\s*([\d.]+,\d{2})\s+([\d.]+,\d{2})",
    re.IGNORECASE,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_date(text: str) -> date | None:
    """Parse une date DD/MM/YYYY."""
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", text.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _parse_montant(text: str) -> Decimal:
    """Convertit '1.311,00' ou '24,99*' en Decimal.

    SG utilise le point comme séparateur de milliers et la virgule pour la décimale.
    Le suffixe '*' signale une opération exonérée de commission (non pertinent ici).
    """
    cleaned = text.strip().rstrip("*").replace(".", "").replace(",", ".")
    return Decimal(cleaned)


def _last_amount(rest: str) -> Decimal | None:
    """Extrait le dernier montant d'une ligne de transaction."""
    matches = _AMOUNT_RE.findall(rest)
    if not matches:
        return None
    try:
        return _parse_montant(matches[-1])
    except InvalidOperation:
        return None


def _strip_trailing_amount(rest: str) -> str:
    """Retire le montant de fin de ligne pour obtenir le libellé brut."""
    matches = list(_AMOUNT_RE.finditer(rest))
    if not matches:
        return rest.strip()
    return rest[: matches[-1].start()].strip()


# ─── Extraction des montants débit/crédit depuis la table ────────────────────


def _extract_page_amounts(
    tables: list[list[list[str | None]]],
) -> tuple[list[Decimal], list[Decimal]]:
    """Extrait les listes ordonnées de débits et crédits depuis les tables SG.

    Structure SG : une table par page, avec UNE ligne de données (plusieurs
    transactions packées). La colonne 3 = débits, colonne 4 = crédits.
    Les lignes SOLDE (col 0 commence par "SOLDE") et TOTAUX (col 0 = None)
    sont ignorées.
    """
    debits: list[Decimal] = []
    credits: list[Decimal] = []

    for table in tables:
        for row in table:
            if not row or len(row) < 5:
                continue
            col0 = (row[0] or "").strip()
            # Ligne SOLDE ou TOTAUX → ignorer
            if not col0:
                continue
            # Ligne de données : col 0 commence par une date DD/MM/YYYY
            if not re.match(r"^\d{2}/\d{2}/\d{4}", col0):
                continue

            col3 = (row[3] or "").strip()
            col4 = (row[4] or "").strip()

            for amt_str in col3.split("\n"):
                amt_str = amt_str.strip()
                if amt_str:
                    try:
                        debits.append(_parse_montant(amt_str))
                    except InvalidOperation:
                        pass

            for amt_str in col4.split("\n"):
                amt_str = amt_str.strip()
                if amt_str:
                    try:
                        credits.append(_parse_montant(amt_str))
                    except InvalidOperation:
                        pass

    return debits, credits


# ─── Parsing des transactions depuis le texte ─────────────────────────────────


def _parse_page_transactions(
    text: str,
    debit_q: list[Decimal],
    credit_q: list[Decimal],
    warnings: list[str],
) -> list[ParsedTransaction]:
    """Parse les transactions d'une page à partir de son texte.

    Chaque transaction débute par une ligne DD/MM/YYYY DD/MM/YYYY …
    Le sens (débit/crédit) est déterminé par matching séquentiel contre les
    listes ordonnées issues des colonnes de la table (debit_q / credit_q).
    """
    # Collecte des transactions brutes (date_op, date_val, libellé, montant)
    raw: list[tuple[str, str, list[str], Decimal | None]] = []
    current: tuple[str, str] | None = None
    current_lines: list[str] = []
    current_amount: Decimal | None = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        m = _TX_LINE_RE.match(line)
        if m:
            if current is not None:
                raw.append((*current, current_lines, current_amount))
            d_op, d_val, rest = m.group(1), m.group(2), m.group(3)
            current = (d_op, d_val)
            current_amount = _last_amount(rest)
            first_lib = _strip_trailing_amount(rest)
            current_lines = [first_lib] if first_lib else []
        elif current is not None:
            if line and not line.lower().startswith("suite"):
                current_lines.append(line)

    if current is not None:
        raw.append((*current, current_lines, current_amount))

    # Attribution du sens par matching séquentiel
    transactions: list[ParsedTransaction] = []
    d_idx = 0
    c_idx = 0

    for d_op_str, d_val_str, lib_lines, amount in raw:
        date_op = _parse_date(d_op_str)
        date_val = _parse_date(d_val_str) or date_op
        if date_op is None:
            warnings.append(f"Date invalide ignorée : {d_op_str!r}")
            continue

        libelle = " / ".join(ln for ln in lib_lines if ln)

        if amount is None:
            warnings.append(f"Montant non extrait pour : {libelle[:60]!r}")
            continue

        # Matching séquentiel : comparer au front de debit_q puis credit_q
        sens: str | None = None
        tol = Decimal("0.01")

        if d_idx < len(debit_q) and abs(amount - debit_q[d_idx]) <= tol:
            sens = "debit"
            d_idx += 1
        elif c_idx < len(credit_q) and abs(amount - credit_q[c_idx]) <= tol:
            sens = "credit"
            c_idx += 1
        elif d_idx < len(debit_q):
            # Essai inversé (cas rare : amount dans crédit mais débit_q en tête)
            if c_idx < len(credit_q) and abs(amount - credit_q[c_idx]) <= tol:
                sens = "credit"
                c_idx += 1
        elif c_idx < len(credit_q):
            if abs(amount - credit_q[c_idx]) <= tol:
                sens = "credit"
                c_idx += 1

        if sens is None:
            warnings.append(
                f"Sens indéterminé pour {libelle[:60]!r} "
                f"(montant={amount}, débit_restants={debit_q[d_idx:]}, "
                f"crédit_restants={credit_q[c_idx:]})"
            )
            continue

        transactions.append(
            ParsedTransaction(
                date_operation=date_op,
                date_valeur=date_val,
                libelle=libelle,
                montant=amount,
                sens=sens,
            )
        )

    if d_idx < len(debit_q):
        warnings.append(
            f"{len(debit_q) - d_idx} débit(s) de la table non consommé(s) : "
            f"{debit_q[d_idx:]}"
        )
    if c_idx < len(credit_q):
        warnings.append(
            f"{len(credit_q) - c_idx} crédit(s) de la table non consommé(s) : "
            f"{credit_q[c_idx:]}"
        )

    return transactions


# ─── Parser principal ─────────────────────────────────────────────────────────


def parse(pdf_path: str | Path) -> ParsedReleve:
    """Parse un relevé Société Générale (mono ou multi-mois) et retourne ses transactions.

    Gestion des doublons : détecte les copies dupliquées par le marqueur
    "Page 1/N" + période (du…au…). Chaque période n'est traitée qu'une seule fois.
    """
    pdf_path = Path(pdf_path)

    transactions: list[ParsedTransaction] = []
    warnings: list[str] = []

    # Accumulation des métadonnées par période (pour multi-mois)
    # { (date_debut, date_fin) -> {"solde_prec": Decimal, "nouveau_solde": Decimal,
    #                               "total_debits": Decimal, "total_credits": Decimal} }
    period_meta: dict[tuple[date, date], dict] = {}
    seen_periods: set[tuple[date, date]] = set()

    skip_group = False
    current_period_key: tuple[date, date] | None = None

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            tables = page.extract_tables() or []

            # Détection de début de groupe mensuel
            if _PAGE1_RE.search(text):
                period_m = _PERIOD_RE.search(text)
                if period_m:
                    d_start = _parse_date(period_m.group(1))
                    d_end = _parse_date(period_m.group(2))
                    if d_start and d_end:
                        key = (d_start, d_end)
                        current_period_key = key
                        if key in seen_periods:
                            skip_group = True
                        else:
                            skip_group = False
                            seen_periods.add(key)
                            period_meta[key] = {}

            if skip_group:
                continue

            # ── Extraction des montants depuis la table ──────────────────────
            debit_q, credit_q = _extract_page_amounts(tables)

            # ── Parsing des transactions depuis le texte ─────────────────────
            page_txs = _parse_page_transactions(text, debit_q, credit_q, warnings)
            transactions.extend(page_txs)

            # ── Collecte des métadonnées de cette page ───────────────────────
            if current_period_key is None:
                continue
            meta_entry = period_meta[current_period_key]

            # Solde précédent (présent uniquement sur la 1re page du mois)
            sp = _SOLDE_PREC_RE.search(text)
            if sp and "solde_prec" not in meta_entry:
                sp_date = _parse_date(sp.group(1))
                try:
                    meta_entry["solde_prec"] = _parse_montant(sp.group(2))
                    meta_entry["solde_prec_date"] = sp_date
                except InvalidOperation:
                    pass

            # Nouveau solde (présent sur la dernière page du mois)
            ns = _NOUVEAU_SOLDE_RE.search(text)
            if ns:
                ns_date = _parse_date(ns.group(1))
                try:
                    meta_entry["nouveau_solde"] = _parse_montant(ns.group(2))
                    meta_entry["nouveau_solde_date"] = ns_date
                except InvalidOperation:
                    pass

            # Totaux mensuels (présents sur la dernière page du mois)
            tot = _TOTAUX_RE.search(text)
            if tot:
                try:
                    meta_entry["total_debits"] = _parse_montant(tot.group(1))
                    meta_entry["total_credits"] = _parse_montant(tot.group(2))
                except InvalidOperation:
                    pass

    # ── Construction de la métadonnée agrégée ────────────────────────────────
    metadata = _build_metadata(period_meta, warnings)

    return ParsedReleve(metadata=metadata, transactions=transactions, warnings=warnings)


def _build_metadata(
    period_meta: dict[tuple[date, date], dict],
    warnings: list[str],
) -> ReleveMetadata:
    """Agrège les métadonnées de tous les mois en une ReleveMetadata cohérente."""
    meta = ReleveMetadata()

    if not period_meta:
        warnings.append("Aucune période détectée dans le relevé.")
        return meta

    # Tri chronologique des périodes
    sorted_periods = sorted(period_meta.keys(), key=lambda k: k[0])

    meta.date_debut = sorted_periods[0][0]
    meta.date_fin = sorted_periods[-1][1]

    # Solde initial = solde précédent de la période la plus ancienne
    first_entry = period_meta[sorted_periods[0]]
    if "solde_prec" in first_entry:
        meta.solde_initial = first_entry["solde_prec"]
    else:
        warnings.append("Solde initial non trouvé dans le premier mois.")

    # Solde final = nouveau solde de la période la plus récente
    last_entry = period_meta[sorted_periods[-1]]
    if "nouveau_solde" in last_entry:
        meta.solde_final = last_entry["nouveau_solde"]
    else:
        warnings.append("Solde final non trouvé dans le dernier mois.")

    # Totaux = somme sur tous les mois
    total_d = Decimal("0")
    total_c = Decimal("0")
    has_totaux = False
    for entry in period_meta.values():
        if "total_debits" in entry and "total_credits" in entry:
            total_d += entry["total_debits"]
            total_c += entry["total_credits"]
            has_totaux = True
        else:
            warnings.append(
                "Totaux manquants pour au moins un mois — validation partielle."
            )

    if has_totaux:
        meta.total_debits = total_d
        meta.total_credits = total_c

    return meta
