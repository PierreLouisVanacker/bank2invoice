# CONTEXT — bank2invoice

> Document de contexte pour reprendre le projet **bank2invoice** dans un nouveau chat
> (typiquement avec Claude Code). Tout ce qui compte est ici : produit, archi,
> décisions, état d'avancement, pièges connus, et ce qui reste à faire.
>
> **Date du snapshot** : mai 2026 — fin du Lot 7 (parser SG)
> **Version code** : 0.7.0

---

## 1. Quoi et pourquoi

**bank2invoice** transforme des PDFs de relevés bancaires en **factures
comptables conformes** (PDF générés via LaTeX), avec :
- extraction automatique des virements entrants
- identification de l'émetteur (nom/prénom ou société) via LLM local
- UI web de review (HTMX) pour valider/corriger
- numérotation continue conforme aux obligations légales françaises
- multi-utilisateurs avec auth complète

**Utilisateur principal du MVP** : **Lorène CARDOT**, avocate
(auto-entrepreneur). Format "forfait" pour ses factures. Banque : **Crédit
Agricole**. Statut TVA à confirmer (initialement franchise art. 293 B).

**Cas d'usage type** : Lorène uploade son relevé mensuel CA → l'app détecte
les virements entrants (clients qui l'ont payée) → elle coche, corrige les
noms si besoin, génère les PDF de factures rétroactivement. C'est un outil
de **régularisation de facturation** (factures émises après le paiement).

---

## 2. Décisions architecturales (le « pourquoi »)

### 2.1. Parser PDF : extract_tables, pas positions x/y, pas LLM

Initialement j'avais tenté positions x/y (mesurer où sont les colonnes Débit
et Crédit). Lorène a suggéré `pdfplumber.extract_tables()` — bien meilleur :
- les colonnes Débit/Crédit sont **séparées par construction**
- les libellés multi-lignes sont déjà fusionnés via `\n` dans la cellule
- moins de code, plus portable inter-banques

**Pas de LLM pour le parsing des transactions** : le format CA est trop régulier
pour justifier le coût/risque LLM. Le LLM ne sert QUE pour l'extraction
d'émetteur depuis le libellé (où le format est sale).

### 2.2. Validations déterministes obligatoires

Après parsing, on vérifie :
- `somme(débits) == total débits affiché sur le relevé`
- `somme(crédits) == total crédits affiché`
- `solde_initial + crédits − débits == solde_final`
- toutes les dates dans la période

Si une validation échoue → relevé en statut **quarantaine**, aucune transaction
insérée. C'est la safety net contre les erreurs d'extraction silencieuses.

### 2.3. LLM Ollama local par défaut

- Données bancaires sensibles → ne pas envoyer à un LLM cloud par défaut
- Abstraction `LLMClient` (Protocol) avec implémentations : `OllamaClient`,
  `StubLLM` (déterministe pour tests), `AnthropicClient` (optionnel)
- Modèle recommandé : `qwen2.5:7b` (bon compromis qualité/vitesse)
- JSON schema strict en sortie pour forcer la structuration

### 2.4. Numérotation continue par user, par année

Format : `AAAA-NNNN`. **Reset annuel** avec préfixe année (2026-0001,
2027-0001). Continue au sein d'une année. Stocké dans `Config` avec clé
`numero_compteur_AAAA_userN`. Garantit la conformité légale (continuité,
chronologie par date d'émission).

### 2.5. Factures figées (append-only)

Une fois générée, une `Facture` ne peut être ni modifiée ni supprimée
(contrainte légale). Le statut TVA, les montants, le numéro sont gravés.
Correction = facture d'avoir + nouvelle facture (non implémenté).

### 2.6. Date facture ≠ date émission

- `date_facture` : affichée sur le PDF, modifiable, défaut = date du virement
- `date_emission` : utilisée pour la numérotation chronologique, non éditable

### 2.7. Calcul TVA côté Python, pas LaTeX

Le template original utilisait le package `fp` pour recalculer HT/TVA/TTC à
partir du HT. **Refactor** : tout le calcul est en Python, le template
affiche juste les 3 valeurs déjà calculées. Évite la double interprétation.

**Convention** : le montant du virement reçu est **TTC**. Donc HT = TTC /
(1 + taux/100), TVA = TTC − HT. (Confirmé par Lorène : « 1000 sur le relevé
= 1000 TTC »).

### 2.8. Aliases : apprentissage incrémental sans ML

Quand on associe un libellé bancaire à un client, on stocke un fragment du
libellé comme `ClientAlias`. Les ingestions suivantes proposent
automatiquement le client si l'alias matche le nouveau libellé. Pas de ML,
juste une table de correspondance qui grandit avec l'usage.

### 2.9. Stack technique

| Choix | Raison |
|---|---|
| **FastAPI** | Async, OpenAPI auto, moderne |
| **HTMX + Jinja2** | Pas de SPA, un seul process, parfait pour table+forms |
| **SQLite + SQLModel** | 1 fichier, ACID, migrable vers Postgres si besoin |
| **pdfplumber** | Texte natif (pas d'OCR), extract_tables |
| **xelatex + Jinja2** | Template LaTeX existant de Lorène, délimiteurs custom |
| **bcrypt + itsdangerous** | Auth standard, sessions cookie signées (pas de table sessions) |

### 2.10. Multi-utilisateurs (Lot 6)

- 2-3 personnes de confiance (toi + Lorène pour l'instant), mais sécurité réelle
- Hash bcrypt rounds=12 (~250ms)
- Sessions cookie signées (stateless, 30 jours, sliding)
- Toutes les tables métier ont un `user_id` → isolation stricte
- Numérotation, profil, PDFs : tout est par user
- Migration legacy automatique au démarrage si données pré-multi-user

---

## 3. Modèle de données (8 tables)

```
users
  id, email (unique), password_hash (bcrypt), nom_affichage,
  created_at, is_active

profil_utilisateur (un par user, lien 1:1 via user_id unique)
  id, user_id, nom, nom_complet, adresse, code_postal, ville, pays,
  siret, ape, numero_tva, email, telephone,
  iban, bic, code_banque, code_guichet, numero_compte, cle_rib,
  assujetti_tva, tva_taux_defaut, mention_legale_non_assujetti,
  mentions_pied_page, lieu_emission, objet_defaut, designation_defaut,
  logo_path

releves
  id, user_id, nom_fichier, banque (CA|SG|None), date_debut, date_fin,
  hash_fichier (SHA256, index — unique par user au niveau service),
  statut (uploaded|parse|erreur|quarantaine), uploaded_at, parsed_at

transactions
  id, releve_id, date, libelle_brut, montant, sens (credit|debit),
  est_virement_entrant,
  emetteur_type, emetteur_nom, emetteur_prenom, emetteur_civilite,
  emetteur_raison_sociale,
  client_id (FK clients, NULL si pas associé),
  inclus (None|True|False), review_status (auto_ok|a_valider|valide|exclu),
  date_facture_override (NULL = utiliser tx.date),
  facture_id (NULL avant génération)

clients
  id, user_id, type (personne|societe), nom, prenom, raison_sociale,
  adresse, code_postal, ville, siret, email, notes, created_at

client_aliases (mémoire incrémentale de matching)
  id, client_id, alias_libelle (index), poids

factures (APPEND-ONLY, contrainte au niveau service)
  id, user_id, numero (index — unique par user via service),
  client_id, transaction_id,
  date_facture, date_emission,
  libelle_prestation,
  montant_ttc, montant_ht, tva_taux, tva_montant,
  mention_legale_tva (figée à la génération),
  pdf_path, status (active|annulee_par_avoir), created_at

config (clé/valeur globale, ex: numero_compteur_2026_user42)
  cle (PK), valeur
```

---

## 4. Pipeline fonctionnel (vue d'ensemble)

```
PDF uploadé (UI ou CLI)
  │
  ├─→ hash SHA256 → idempotence (par user, hash)
  ├─→ pdfplumber.extract_text → detect_banque (CA|SG)
  ├─→ parse_ca.parse() ou parse_sg.parse() → ParsedReleve
  │     metadata (soldes, totaux, période) + transactions
  ├─→ validate() : sommes + soldes (sinon QUARANTAINE)
  ├─→ INSERT Releve
  └─→ Pour chaque transaction :
        ├─ filter_entrants : virement entrant candidat ? exclu ?
        ├─ si entrant :
        │    ├─ emetteur.extract_emetteur (preprocess regex + LLM)
        │    └─ match_client (aliases + nom)
        └─ INSERT Transaction

[Utilisateur valide via UI /releves/{id}]
  │ coche, édite émetteur, associe client, ajuste date
  │
  v
[Bouton "Générer"]
  │
  ├─→ generer_facture(tx, user) pour chaque tx incluse
  │     ├─ idempotence : skip si tx.facture_id existe déjà
  │     ├─ client : associé OU créé depuis émetteur
  │     ├─ profil = ProfilUtilisateur du user
  │     ├─ numero = attribuer_numero(session, user.id, today)
  │     ├─ calcul HT/TVA/TTC (TTC fixé = tx.montant)
  │     ├─ render Jinja2 → header.tex + main.tex + footer.tex
  │     ├─ compile_facture : xelatex 2 passes
  │     └─ INSERT Facture (append-only, user_id, montants figés)
  │
  └─→ PDF dans data/factures/user_X/AAAA/AAAA-NNNN_NomClient.pdf
```

---

## 5. Arborescence du projet

```
bank2invoice/
├── pyproject.toml
├── README.md
├── CADRAGE.md            (spec produit complète)
├── CONTEXT.md            (CE fichier)
├── .env.example
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── main.py           FastAPI app + exception_handler 401→redirect login
│   ├── config.py         Settings via pydantic-settings (.env)
│   ├── db.py             SQLModel engine + _apply_light_migrations (idempotent)
│   ├── models.py         8 tables SQLModel
│   ├── cli.py            seed / adduser / reset
│   ├── auth/
│   │   ├── passwords.py  bcrypt hash/verify (rounds=12)
│   │   ├── sessions.py   itsdangerous TimestampSigner (cookies signés, 30j)
│   │   └── deps.py       current_user, optional_current_user (FastAPI Depends)
│   ├── routers/
│   │   ├── auth.py       login / register / logout (publics)
│   │   ├── profil.py     /profil GET (form) + POST (save) + POST /tva (toggle HTMX)
│   │   ├── releves.py    /releves liste + /{id} review + /upload (drag&drop)
│   │   ├── transactions.py  édition inline HTMX + panneau détail + create-client-from-tx
│   │   ├── clients.py    CRUD clients
│   │   └── factures.py   POST /generate/{releve_id} + liste + GET /{id}/pdf
│   ├── pipeline/
│   │   ├── extract_pdf.py    (utilitaire générique positions x/y, peu utilisé)
│   │   ├── detect_banque.py  marqueurs texte CA, SG
│   │   ├── parse_ca.py       parser CA via extract_tables (déterministe)
│   │   ├── parse_sg.py       parser SG text-based + matching séquentiel débit/crédit
│   │   ├── validate.py       validations sommes/soldes
│   │   ├── filter_entrants.py règles inclusion/exclusion (CARPA exclu, préfixes CA+SG)
│   │   ├── emetteur.py       preprocess regex + appel LLM
│   │   ├── match_client.py   aliases + nom/raison_sociale
│   │   ├── ingest.py         service d'ingestion idempotent (par user, hash)
│   │   ├── ingest_cli.py     CLI : python -m app.pipeline.ingest_cli --user-id N pdf...
│   │   └── inspect_cli.py    CLI inspect : relevés, transactions, entrants
│   ├── llm/
│   │   ├── base.py       Protocol LLMClient + EmetteurExtrait + JSON schema
│   │   ├── ollama.py     HTTP /api/chat avec format JSON schema
│   │   ├── stub.py       déterministe (tests, fallback)
│   │   └── factory.py    get_llm_client() selon settings.llm_provider
│   ├── invoice/
│   │   ├── latex_escape.py  passe unique caractère-par-caractère
│   │   ├── render.py        env Jinja2 LaTeX-safe + compile_facture (xelatex)
│   │   ├── numerotation.py  par user, par année, séquentiel
│   │   ├── generate.py      generer_facture(tx, session, user, date_facture)
│   │   └── templates/       (sources LaTeX templatisées)
│   │       ├── facture_header.tex.j2
│   │       ├── facture_footer.tex.j2
│   │       └── facture_main.tex.j2
│   └── templates/         (HTML/HTMX Jinja2)
│       ├── base.html      nav + CSS global + spinner overlay + panel-slot
│       ├── auth/
│       │   ├── _layout.html  layout dédié (sans nav app)
│       │   ├── login.html
│       │   └── register.html
│       ├── profil/
│       │   ├── view.html       formulaire éditable complet
│       │   └── _tva_badge.html fragment OOB
│       ├── releves/
│       │   ├── list.html       dropzone upload + table relevés
│       │   └── detail.html     review + bouton Générer
│       ├── transactions/
│       │   ├── _table.html     <table> wrapper
│       │   ├── _row.html       <tr> contenu (réutilisé en swap HTMX)
│       │   └── _panel.html     panneau latéral overlay
│       ├── clients/
│       │   ├── list.html
│       │   └── _form.html      panneau create/edit
│       └── factures/
│           ├── list.html       registre
│           └── _generation_result.html
├── data/                  (.gitignore : tous les contenus)
│   ├── db.sqlite
│   ├── pdfs/user_X/       PDFs uploadés par user
│   └── factures/user_X/AAAA/   PDFs générés par user/année
└── tests/
    ├── test_invoice_render.py
    ├── test_parse_ca.py       (16 tests, demande PDF de réf)
    └── test_lot2.py           (filtrage, emetteur, ingestion)
```

---

## 6. État d'avancement (lots livrés)

| Lot | Contenu | État |
|---|---|---|
| 0 | Setup projet, modèles, seed profil, page profil HTMX | ✅ |
| 1 | Parser CA + détection banque + validations | ✅ |
| 2 | Persistance + filtrage + emetteur LLM + matching client | ✅ |
| 3 | UI complète (relevés, review, clients, panneau overlay) | ✅ |
| 4 | Numérotation + génération PDF LaTeX + upload UI + registre | ✅ |
| 5 | Créer client depuis review + date modifiable + profil éditable | ✅ |
| 6 | TVA correcte + spinners + multi-users auth complet | ✅ (non testé en sandbox) |
| 7 | Parser Société Générale (parse_sg.py) | ✅ |

---

## 7. Lots non encore traités

Il existe aussi un Lot 8+ envisagé :
- Factures d'avoir (annulation/correction conforme)
- Multi-template (forfait/horaire/journalier) — pour l'instant un seul template
- Export comptable (CSV pour comptable)
- Upload multi-PDF en une fois
- Tests pytest adaptés au multi-users (les tests Lot 2 cassent sans cookie —
  les 3 tests `test_match_par_*` échouent avec IntegrityError faute de user_id)

---

## 8. Pièges connus et gotchas

### 8.1. Templates Jinja2 LaTeX-safe

Délimiteurs custom pour ne pas conflicter avec LaTeX :
- `\VAR{x}` au lieu de `{{ x }}`
- `\BLOCK{ if x }` au lieu de `{% if x %}`
- `\#{ comment }` au lieu de `{# comment #}`

**Conséquence** : **JAMAIS écrire `\VAR{...}` dans un commentaire LaTeX**,
même un `%`-commentaire. Jinja2 essaie de l'évaluer comme variable et
plante. Cf. commit "Template Jinja2 (delimiters VAR et BLOCK)".

### 8.2. SyntaxWarning sur backslashes dans docstrings Python

Les docstrings qui contiennent `\V`, `\B` etc. doivent être en raw string
(`r"""..."""`). Cf. `app/invoice/render.py`.

### 8.3. latex_escape — passe unique

Ne JAMAIS faire `str.replace` en chaîne pour échapper LaTeX. Les `{` et `}`
introduits par `\textbackslash{}` seraient ré-échappés. Implémentation
correcte : tokenisation caractère par caractère en une seule passe.

### 8.4. xelatex sur Windows

- Doit être dans le PATH système, pas dans le venv
- MiKTeX télécharge les packages à la volée → première compilation lente
- Caractères `º` (ordinal masculin) cassent sous `fontenc T1` → utiliser
  `\textnumero` du package `textcomp` (déjà fait)

### 8.5. pdfplumber et montants ≥1000€

pdfplumber peut séparer `"1 000,00"` en 2 mots dans extract_words. Avec
l'approche extract_tables, les valeurs arrivent fusionnées dans une seule
cellule donc plus de problème.

### 8.6. HTMX TemplateResponse

Depuis Starlette ≥0.29, la signature est `TemplateResponse(request, name,
context)` — pas `TemplateResponse(name, {"request": request, ...})`. La
mauvaise signature donne une erreur cryptique `TypeError: unhashable type:
'dict'`.

### 8.7. Out-of-band swap HTMX et duplication d'id

Les fragments OOB (`<span hx-swap-oob="true" id="X">`) ne doivent **pas**
exister au rendu initial. Sinon plusieurs éléments ont le même `id` et
HTMX les accumule à chaque interaction. Cf. fix du compteur "À inclure" :
condition `{% if count_inclus is defined and count_inclus is not none %}`.

Piège Jinja2 lié : `Undefined is not None` vaut `True`. Donc tester
`is defined` AVANT `is not none`.

### 8.8. Checkbox HTMX sans name

Une checkbox sans `name=` n'envoie aucune valeur dans le POST. HTMX semble
fonctionner (la requête part) mais le serveur reçoit toujours `""` =
False. Symptôme : "rien ne se passe quand je coche".

### 8.9. Migration légère SQLite

`SQLModel.metadata.create_all()` ne fait PAS de migration : il crée les
tables manquantes mais n'ajoute pas les colonnes ajoutées après coup. On a
un `_apply_light_migrations()` qui :
- `PRAGMA table_info` pour lister les colonnes existantes
- `ALTER TABLE … ADD COLUMN` si manquante (idempotent)
- Pour le Lot 6 : si lignes orphelines (`user_id IS NULL`), créer un user
  "legacy" et les rattacher

Pour des migrations complexes (renommage, contraintes), passer à Alembic.

### 8.10. Setuptools auto-discovery sur Windows

`pyproject.toml` doit déclarer explicitement les packages (`[tool.setuptools]
packages = ["app", "app.routers", ...]`), sinon setuptools tombe sur
plusieurs candidats à la racine (`app/` ET `data/`) et refuse.

### 8.11. __init__.py vides perdus à l'unzip Windows

L'Explorateur Windows perd parfois les fichiers de 0 octet à l'extraction.
Les `__init__.py` du projet contiennent une docstring `"""Package marker."""`
pour ne pas faire 0 octet. De même les `.gitkeep` sous `data/` ont du
contenu.

### 8.12. Décision métier CARPA

La CARPA (Caisse des Règlements Pécuniaires des Avocats) n'est PAS un
client à facturer pour Lorène. Ce sont des mouvements de fonds de tiers.
**Exclusion en dur** dans `filter_entrants.py`. Documenté.

### 8.13. Particularités du parser SG (parse_sg.py)

Le format SG est radicalement différent du CA :

**Structure table** : toutes les transactions d'une page sont packées dans
**une seule ligne de table** (vs une ligne par transaction pour CA). Les colonnes
débit (col 3) et crédit (col 4) contiennent des montants séparés par `\n`.

**Séparateur milliers** : SG utilise le point (`1.311,00`) — CA utilise l'espace
(`1 311,20`). La fonction `_parse_montant` de parse_sg retire les `.` avant de
convertir (ne pas réutiliser celle de parse_ca).

**Détection débit/crédit** : matching séquentiel — on extrait le dernier montant
de chaque ligne de transaction depuis le texte, puis on le compare au front de
`debit_q` puis `credit_q` (listes ordonnées issues de la table). Cela exploite
le fait que table et texte parcourent les transactions dans le même ordre.

**Cas REGULVERST** : `REGULVERST07/02/25 1000,00 20,00` — les `1000,00` sont
intégrés dans le libellé (montant régularisé), le vrai montant est `20,00` (le
dernier). La regex `(?<!\d)\d{1,3}(?:\.\d{3})*,\d{2}\*?(?!\d)` avec lookbehind
négatif extrait correctement `20,00` uniquement, car `1000,00` a 4 chiffres sans
séparateur de milliers → ne matche pas `\d{1,3}`.

**PDFs multi-mois avec doublons** : un fichier SG peut contenir plusieurs mois
ET des copies dupliquées (copie client + copie banque). parse_sg détecte les
doublons via le marqueur `Page 1/N` dans l'en-tête combiné à la période
`du…au…`. Chaque période n'est parsée qu'une fois. La validation agrège les
totaux de tous les mois (solde_initial = ouverture du plus ancien mois,
solde_final = clôture du plus récent).

**Préfixes virement SG** : dans les libellés SG, les mots sont fusionnés sans
espace (`VIRRECU3499075591S`, pas `VIR RECU 3499075591S`). filter_entrants.py
a été mis à jour avec les préfixes `virrecu`, `virinstre`, `versementexpress`.
Le pattern CARPA ajoute `caissedesreglements` (sans espaces) pour matcher le
format SG.

### 8.14. SESSION_SECRET obligatoire

Si vide ou < 16 caractères → `RuntimeError` au premier login. Générer avec :
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 8.14. Migration legacy au démarrage

Au démarrage, si des lignes ont `user_id IS NULL`, un user "legacy" est
créé avec email `legacy@bank2invoice.local` et un mot de passe aléatoire
**affiché une seule fois dans la console**. Ne pas rater ce log au premier
démarrage post-Lot 6.

---

## 9. Configuration .env

```
DATABASE_URL=sqlite:///data/db.sqlite

PDFS_DIR=data/pdfs
FACTURES_DIR=data/factures

LLM_PROVIDER=ollama          # ou 'stub' (tests sans Ollama)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

LATEX_BINARY=xelatex         # ou pdflatex ; peut être un chemin absolu

SESSION_SECRET=<32 chars min, généré avec secrets.token_urlsafe(32)>

DEBUG=true
```

---

## 10. Commandes utiles

```bash
# Setup initial
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # éditer, mettre un SESSION_SECRET
python -m app.cli seed

# Créer un user en CLI (alternative à /register)
python -m app.cli adduser

# Reset complet (DESTRUCTIF)
python -m app.cli reset

# Ingestion CLI (multi-user : --user-id obligatoire)
python -m app.pipeline.ingest_cli --user-id 1 data/pdfs/releve.pdf
python -m app.pipeline.ingest_cli --user-id 1 --no-llm data/pdfs/releve.pdf

# Inspection
python -m app.pipeline.inspect_cli releves
python -m app.pipeline.inspect_cli transactions 1
python -m app.pipeline.inspect_cli entrants 1
python -m app.pipeline.inspect_cli entrants

# Lancer l'app
uvicorn app.main:app --reload

# Tests
pytest -v
```

---

## 11. Workflow utilisateur cible

1. `pip install -e ".[dev]"` puis configurer `.env` (`SESSION_SECRET`)
2. `uvicorn app.main:app --reload` → ouvrir `http://localhost:8000/`
3. `/register` : créer un compte (Lorène, ou un user de test)
4. `/profil` : compléter le profil complet (adresse, IBAN, SIRET, etc.)
5. `/releves` : glisser-déposer un PDF de relevé CA
6. Cliquer "Review →" sur le relevé
7. Pour chaque virement entrant :
   - coche "Inclure"
   - clique "Détails" pour ouvrir le panneau
   - corriger l'émetteur si besoin (le LLM peut inverser nom/prénom)
   - cliquer "+ Créer ce client" (fiche minimale créée) OU sélectionner un
     client existant OU laisser "Aucun" (sera créé auto à la génération)
   - ajuster la date de facture si besoin
8. `/clients` : compléter les adresses des clients créés (pour conformité)
9. Retour sur la review, cliquer "Générer les factures cochées"
10. `/factures` : télécharger les PDF

---

## 12. Points encore ouverts / décisions à confirmer

- **Statut TVA réel de Lorène** : laissé en franchise par défaut au seed
- **Code APE** : vide
- **Liste des IBAN/comptes perso à exclure** : juste "vers compte fortuneo",
  "vers compte joint", "vers compte epargne" en dur dans `filter_entrants.py`
- **Adresse obligatoire sur facture** : recommandée, non bloquante
- **Multi-template** : MVP = template forfait uniquement (celui de Lorène)

---

## 13. Prompts utiles pour Claude Code

### Pour reprendre le développement

> Voici un projet FastAPI/HTMX/SQLite qui transforme des relevés bancaires
> PDF en factures via LaTeX, avec multi-utilisateurs. Lis `CONTEXT.md` et
> `CADRAGE.md` à la racine pour le contexte complet. L'architecture est
> stable, je veux travailler sur [X].

### Pour débugger

> Lis `CONTEXT.md` section "Pièges connus" — il y a probablement déjà la
> réponse au bug que tu vas rencontrer. Sinon, vérifie les modèles dans
> `app/models.py`, le flux dans `app/pipeline/ingest.py` et
> `app/invoice/generate.py`, et les décisions section "Décisions
> architecturales".

### Pour ajouter une banque

Deux parsers existent en référence :
- **parse_ca.py** : tables bien structurées, une ligne par transaction, colonnes
  nommées → approche `extract_tables` + matching par nom de colonne
- **parse_sg.py** : transactions packées dans une seule ligne de table, pas de
  colonnes nommées → approche text-based + matching séquentiel débit/crédit

Pour une nouvelle banque :
> 1. Ajouter ses marqueurs dans `app/pipeline/detect_banque.py`
> 2. Créer `app/pipeline/parse_XX.py` en renvoyant un `ParsedReleve`
>    (importé depuis `parse_ca` — type partagé)
> 3. Enregistrer dans `_PARSERS` de `app/pipeline/ingest.py`
> 4. Ajouter ses préfixes de virement dans `filter_entrants.py`
> 5. Tester sur un PDF réel : `parse_XX.parse(pdf)` + `validate(result)` doit
>    passer avec 0 erreur et 0 warning
> 6. Vérifier que `detect_banque` identifie correctement le PDF avant d'ingérer

### Pour ajouter un template de facture

> Le template LaTeX vit dans `app/invoice/templates/`. Les délimiteurs
> Jinja2 sont `\VAR{...}` et `\BLOCK{ ... }`. JAMAIS de `\VAR{...}` dans
> un commentaire `%`. Toutes les valeurs dynamiques doivent passer par le
> filtre `latex_escape` côté Python. Pour un nouveau template (horaire
> par exemple), créer un sous-dossier `templates/horaire/` et adapter
> `compile_facture()` pour choisir selon un champ profil.

---

## 14. Récap décisions métier importantes

- **Lorène est avocate, mode forfait, Crédit Agricole**
- **Dilawar BALLAL (Paris 16 ESPACE PRO) utilise Société Générale** — second utilisateur
  identifié sur le PDF "Releves 01.25 - BD - complet.pdf" (Jan–Jul 2025)
- **CARPA = exclu** (mouvement de fonds, pas honoraires) — valable CA et SG
- **Montant relevé = TTC** (1000 sur le relevé → 833,33 HT + 166,67 TVA)
- **Statut TVA figé par facture** (pas suiveur du profil après émission)
- **Factures = append-only**, corrections par avoir
- **Numérotation reset annuel**, format AAAA-NNNN, par user
- **Multi-user pour 2-3 personnes de confiance** (toi + Lorène)
- **Pas de mention "Bon pour accord", pas de signature** dans le MVP

---

## 15. Contacts et fichiers de référence

- **Template LaTeX original de Lorène** : commit initial du projet, fichiers
  `facture_header.tex`, `facture_footer.tex`, `main.tex` (à demander à
  l'utilisateur si besoin)
- **PDF de test CA** : `data/pdfs/user_X/releve_ca_2026_01.pdf` (gitignoré)
- **PDF de test SG** : `Releves 01.25 - BD - complet.pdf` à la racine du projet
  (gitignoré — contient Jan–Jul 2025, 21 pages dont doublons mars et mai)
- **Tests qui valident le parsing CA** : `tests/test_parse_ca.py` — 16 tests,
  prouvent l'extraction correcte sur le relevé janvier 2026 (26 transactions,
  validations sommes/soldes au centime près)
- **Validation parser SG** : exécuter `python -c "from app.pipeline import parse_sg,
  validate; r=parse_sg.parse('Releves 01.25 - BD - complet.pdf');
  v=validate.validate(r); print(v.ok, len(r.transactions))"` → doit afficher
  `True 111`
