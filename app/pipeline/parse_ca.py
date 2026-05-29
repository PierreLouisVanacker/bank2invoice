"""Parser de relevé Crédit Agricole.

Stratégie : extraction tabulaire via pdfplumber.extract_tables().

Le PDF du Crédit Agricole expose un tableau natif avec colonnes nommées
("Date opé.", "Date valeur", "Libellé des opérations", "Débit", "Crédit"), que
pdfplumber sait reconnaître. On travaille donc sur des lignes structurées
plutôt que sur des coordonnées x/y.

Avantages vs approche "positions" :
  - distinction débit/crédit "par construction" (colonne vide ↔ non concerné)
  - multi-lignes déjà fusionnées par pdfplumber dans la même cellule via \\n
  - code générique réutilisable pour la Société Générale (Lot 1b)

Cas particuliers gérés manuellement :
  - lignes "filles" avec dates vides → fusion avec la transaction précédente
  - lignes "Total des opérations" / "Nouveau solde" / "Ancien solde" → filtrées
  - colonne cases à cocher (þ/¨) → ignorée
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pdfplumber

# Mois français → numéro (pour parser "31 Janvier 2026")
_MOIS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

# Configuration des noms de colonnes attendus dans l'en-tête du tableau CA.
# Tolérant aux variations (retours à la ligne, espaces).
_HEADER_PATTERNS: dict[str, tuple[str, ...]] = {
    "date_op": ("Date opé", "Date\nopé"),
    "date_val": ("Date valeur", "Date\nvaleur"),
    "libelle": ("Libellé des opérations", "Libellé"),
    "debit": ("Débit",),
    "credit": ("Crédit",),
}


# ─── Types de sortie ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedTransaction:
    date_operation: date
    date_valeur: date
    libelle: str
    montant: Decimal  # toujours positif
    sens: str  # 'credit' | 'debit'


@dataclass
class ReleveMetadata:
    date_debut: date | None = None
    date_fin: date | None = None
    solde_initial: Decimal | None = None
    solde_final: Decimal | None = None
    total_debits: Decimal | None = None
    total_credits: Decimal | None = None


@dataclass
class ParsedReleve:
    metadata: ReleveMetadata
    transactions: list[ParsedTransaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ─── Helpers ────────────────────────────────────────────────────────────────


_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})$")
_MONTANT_RE = re.compile(r"^\d{1,3}(?:\s\d{3})*,\d{2}$")


def _parse_montant(text: str) -> Decimal:
    """Convertit '1 231,20' (ou '1 231.20') en Decimal."""
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    return Decimal(cleaned)


def _parse_date_jj_mm(text: str, year: int) -> date | None:
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    try:
        return date(year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _find_column_indices(header_row: list[str]) -> dict[str, int]:
    """Mappe nom logique → index colonne dans le tableau extrait."""
    indices: dict[str, int] = {}
    for logical_name, patterns in _HEADER_PATTERNS.items():
        for i, cell in enumerate(header_row):
            if cell is None:
                continue
            cell_norm = cell.strip()
            if any(p in cell_norm for p in patterns):
                indices[logical_name] = i
                break
    return indices


def _is_header_row(row: list[str]) -> bool:
    """True si la ligne ressemble à l'en-tête du tableau."""
    if not row:
        return False
    joined = " ".join((c or "") for c in row)
    return "Libellé" in joined and "Débit" in joined and "Crédit" in joined


def _extract_text(pdf_path: str | Path) -> tuple[str, list[list[list[str]]]]:
    """Renvoie (texte complet, liste de tables par page)."""
    pages_text: list[str] = []
    all_tables: list[list[list[str]]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")
            tables = page.extract_tables() or []
            all_tables.extend(tables)
    return "\n".join(pages_text), all_tables


# ─── Métadonnées (extraction depuis le texte brut) ──────────────────────────


def _extract_metadata(text: str) -> ReleveMetadata:
    """Extrait soldes, totaux et période depuis le texte du relevé."""
    meta = ReleveMetadata()

    # Date d'arrêté → date_fin
    m = re.search(r"[Dd]ate d['']arrêté\s*:\s*(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if m:
        month_num = _MOIS_FR.get(m.group(2).lower())
        if month_num:
            try:
                meta.date_fin = date(int(m.group(3)), month_num, int(m.group(1)))
            except ValueError:
                pass

    # Solde initial
    m = re.search(
        r"Ancien solde (?P<sens>créditeur|débiteur) au "
        r"(\d{2})\.(\d{2})\.(\d{4})\s+(?P<mt>[\d\s]+,\d{2})",
        text,
    )
    if m:
        dt = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
        meta.date_debut = dt + timedelta(days=1)
        mt = _parse_montant(m.group("mt"))
        meta.solde_initial = -mt if m.group("sens") == "débiteur" else mt

    # Solde final
    m = re.search(
        r"Nouveau solde (?P<sens>créditeur|débiteur) au "
        r"\d{2}\.\d{2}\.\d{4}\s+(?P<mt>[\d\s]+,\d{2})",
        text,
    )
    if m:
        mt = _parse_montant(m.group("mt"))
        meta.solde_final = -mt if m.group("sens") == "débiteur" else mt

    # Totaux
    m = re.search(
        r"Total des opérations\s+([\d\s]+,\d{2})\s+([\d\s]+,\d{2})", text
    )
    if m:
        meta.total_debits = _parse_montant(m.group(1))
        meta.total_credits = _parse_montant(m.group(2))

    return meta


# ─── Parser principal ───────────────────────────────────────────────────────


# Préfixes de libellé indiquant qu'une ligne n'est pas une transaction
# (totaux, soldes, etc.) — à filtrer même si dates vides.
_STOP_LIBELLE_PREFIXES = (
    "Ancien solde",
    "Nouveau solde",
    "Total des opérations",
)


def parse(pdf_path: str | Path) -> ParsedReleve:
    """Parse un relevé Crédit Agricole et retourne ses transactions."""
    text, all_tables = _extract_text(pdf_path)
    metadata = _extract_metadata(text)
    annee = metadata.date_fin.year if metadata.date_fin else date.today().year

    transactions: list[ParsedTransaction] = []
    warnings: list[str] = []

    # On parcourt toutes les tables de toutes les pages. Chaque table a son
    # en-tête (qu'on identifie pour savoir où sont les colonnes).
    column_indices: dict[str, int] = {}

    for table in all_tables:
        for row in table:
            if not row:
                continue

            # Nettoyage : remplacer None par ''
            row = [(c or "") for c in row]

            # Détection en-tête → on calcule les indices de colonnes pour
            # cette table (et les suivantes si pas redéfini)
            if _is_header_row(row):
                column_indices = _find_column_indices(row)
                continue

            if not column_indices:
                continue  # on a pas encore vu l'en-tête, on ignore

            idx_date_op = column_indices.get("date_op")
            idx_date_val = column_indices.get("date_val")
            idx_libelle = column_indices.get("libelle")
            idx_debit = column_indices.get("debit")
            idx_credit = column_indices.get("credit")

            if None in (idx_date_op, idx_libelle, idx_debit, idx_credit):
                warnings.append("Colonnes manquantes dans l'en-tête détecté")
                continue

            date_op_text = row[idx_date_op].strip()
            libelle_text = row[idx_libelle].strip()
            debit_text = row[idx_debit].strip()
            credit_text = row[idx_credit].strip()

            # Filtres de lignes non-transactionnelles
            if any(libelle_text.startswith(p) for p in _STOP_LIBELLE_PREFIXES):
                continue

            date_op = _parse_date_jj_mm(date_op_text, annee)

            if date_op is None:
                # Ligne sans date → ligne fille à rattacher à la transaction
                # précédente (si présente et si non vide).
                if libelle_text and transactions:
                    last = transactions[-1]
                    transactions[-1] = ParsedTransaction(
                        date_operation=last.date_operation,
                        date_valeur=last.date_valeur,
                        libelle=f"{last.libelle} / {libelle_text}",
                        montant=last.montant,
                        sens=last.sens,
                    )
                continue

            # Ligne de transaction principale
            date_val_text = row[idx_date_val].strip() if idx_date_val is not None else ""
            date_val = _parse_date_jj_mm(date_val_text, annee) or date_op

            # Sens et montant : on lit les colonnes Débit / Crédit
            if debit_text and credit_text:
                warnings.append(
                    f"Débit et Crédit tous deux remplis : {libelle_text!r}"
                )
                continue
            if debit_text:
                sens = "debit"
                montant_text = debit_text
            elif credit_text:
                sens = "credit"
                montant_text = credit_text
            else:
                # Pas de montant : probablement une ligne info à ignorer
                continue

            try:
                montant = _parse_montant(montant_text)
            except Exception as e:
                warnings.append(f"Montant illisible '{montant_text}': {e}")
                continue

            # Si le libellé contient déjà des \n (multi-lignes fusionnées par
            # pdfplumber), on remplace par ' / ' pour cohérence avec les filles.
            libelle_clean = " / ".join(
                ln.strip() for ln in libelle_text.split("\n") if ln.strip()
            )

            transactions.append(
                ParsedTransaction(
                    date_operation=date_op,
                    date_valeur=date_val,
                    libelle=libelle_clean,
                    montant=montant,
                    sens=sens,
                )
            )

    return ParsedReleve(metadata=metadata, transactions=transactions, warnings=warnings)
