# bank2invoice

Génère des factures comptables conformes à partir de PDFs de relevés bancaires.

Voir [`CADRAGE.md`](CADRAGE.md) pour la spec complète.

## Statut

**Lot 5 — livré** : confort d'usage (création client en 1 clic, dates, profil éditable).

Nouveautés Lot 5 :
- **Créer un client depuis la review** : bouton « + Créer ce client » dans le panneau détails, pré-rempli avec l'émetteur extrait. Plus besoin de quitter l'écran.
- **Date de facture modifiable** : champ date dans le panneau (défaut = date du virement). La numérotation reste chronologique par date d'émission.
- **Profil éditable via UI** : la page `/profil` est un formulaire complet (plus besoin du seed CLI pour modifier).
- **Migration légère** : les bases existantes sont mises à jour automatiquement (ajout de colonne sans perte de données).

Tout le pipeline précédent reste fonctionnel : upload PDF, review, génération PDF, numérotation, registre.

## Prérequis

- Python 3.11+
- LaTeX avec `xelatex` dans le PATH (TeX Live, MiKTeX)
- Ollama + modèle (optionnel) : `ollama pull qwen2.5:7b`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate     # ou .venv\Scripts\activate sous Windows
pip install -e ".[dev]"
cp .env.example .env
python -m app.cli seed
uvicorn app.main:app --reload
```

Ouvre http://localhost:8000/

**Mise à jour depuis une version antérieure** : pas besoin de reset. Au démarrage, la base est migrée automatiquement (ajout de la colonne `date_facture_override`).

## Workflow complet

1. **Upload** un PDF sur `/releves` (glisser-déposer)
2. **Review** : coche les virements à facturer
3. **Panneau "Détails"** sur une ligne :
   - corrige l'émetteur si besoin
   - **clique « + Créer ce client »** → client créé et associé en 1 clic
   - ajuste la **date de facture** si besoin
4. **Complète l'adresse** des clients sur `/clients` (pour conformité)
5. **Génère** les factures cochées
6. **Télécharge** depuis `/factures`

## Configuration .env

```
LLM_PROVIDER=ollama          # ou 'stub'
OLLAMA_MODEL=qwen2.5:7b
LATEX_BINARY=xelatex
```

## Tests

```bash
pytest -v
```

## Notes comptables

- Numérotation continue AAAA-NNNN, reset annuel, chronologique par date d'émission
- Factures figées après émission (statut TVA, montants, numéro gravés)
- Date facture (affichée) ≠ date émission (numérotation)

## Lots à venir

- **6** — factures d'avoir, multi-template (forfait/horaire), export comptable, edge cases
