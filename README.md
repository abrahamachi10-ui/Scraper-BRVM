# Scraper BRVM

Pipeline de données BRVM (Bourse Régionale des Valeurs Mobilières) : scraping
quotidien des cours, sociétés, fondamentaux, dividendes et news, génération de
signaux quantitatifs, dashboard HTML de visualisation, et base PostgreSQL
relationnelle pour interroger l'ensemble par requêtes SQL.

## Composants

### 1. Scrapers

| Script | Source | Fonction | Sortie |
|---|---|---|---|
| `scraper_brvm.py` | sikafinance.com | Historique OHLCV des actions et indices | `data/actions/*.csv`, `data/indices/*.csv` |
| `scraper_info_societes.py` | sikafinance.com | Fiches sociétés (capi, flottant, secteur, etc.) | `data/societes/*.json`, `data/indices/*_info.json` |
| `scraper_fondamentaux.py` | sikafinance.com | Fondamentaux 5 ans (CA, RN, BNPA, PER, dividende) | `data/fondamentaux/*.json` |
| `scraper_dividendes.py` | brvm.org + sikafinance.com | Dividendes (à venir/passés) fusionnés + avis PDF | `data/dividendes/*.csv`, `*.json` |
| `scraper_news_brvm.py` | sikafinance.com | News financières BRVM | `data/news/*.csv` |

Modules partagés :

| Module | Rôle |
|---|---|
| `brvm_tickers.py` | Liste centralisée des tickers (48 actions, 13 indices) |
| `brvm_emetteur_mapping.py` | Mapping émetteur BRVM ↔ ticker ↔ nom canonique |
| `brvm_common.py` | Helpers HTTP/logging/parsing communs aux scrapers |

### 2. Signaux & composition

| Script | Fonction | Sortie |
|---|---|---|
| `generate_signals.py` | Signaux Prophet + Black-Litterman (+ backtest) | `data/signals/*` |
| `build_signals_history.py` | Historique pluriannuel des signaux | `data/signals/*` |
| `build_composition_flottante.py` | Composition flottante des indices | `Composition_flottante.xlsx` |

Le moteur quantitatif est dans `quant/` (backtest, covariance, portfolio,
signals), couvert par la suite de tests `tests/`.

### 3. Dashboards

- `brvm_dashboard_enriched.html` — dashboard statique consolidant cours,
  fondamentaux, dividendes et signaux à partir des fichiers de `data/`.
- `dash brvm Power BI.pbip` — rapport Power BI multi-pages (Vue d'ensemble,
  Détail action, Détail indice) connecté au dépôt GitHub et actualisé
  automatiquement. Voir [POWERBI.md](POWERBI.md).

### 4. Base de données PostgreSQL

Les fichiers plats de `data/` sont chargés dans une base relationnelle
PostgreSQL (`brvm`) : 7 tables reliées par clés étrangères, import idempotent
(rejouable sans créer de doublons), et un rapprochement automatique — mais
volontairement prudent — entre les articles de news et les sociétés
concernées.

| Fichier | Rôle |
|---|---|
| `database/schema.sql` | Schéma relationnel : 7 tables, clés étrangères, contraintes de cohérence (`CHECK`) |
| `database/import_data.py` | Import/upsert de `data/` vers PostgreSQL (`ON CONFLICT` → rejouable sans doublons) |
| `database/link_news.py` | Rapprochement news ↔ actions par mots-clés, uniquement quand une seule société correspond au titre |
| `database/test_queries.sql` | 10 requêtes d'exemple (jointures, CTE, window functions) |

**Schéma** — deux tables pivot (`actions`, `indices`) référencées par les
autres :

```
actions ──┬── historique_actions   (cours quotidiens)
          ├── dividendes           (par exercice)
          ├── fondamentaux         (CA, RN, BNPA, PER par année)
          └── news                 (articles liés, si identifiés)

indices ──┴── historique_indices   (cours quotidiens)
```

**Volumétrie actuelle** : 48 actions, 13 indices, 152 000+ lignes de cours
(actions + indices), 326 dividendes, 239 lignes de fondamentaux, 5 600+
articles de news (dont 230 liés automatiquement à une société sans
ambiguïté).

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

### Scrapers (manuels)

```bash
python scraper_brvm.py             # cours actions + indices
python scraper_info_societes.py    # fiches sociétés
python scraper_fondamentaux.py     # fondamentaux 5 ans
python scraper_dividendes.py       # dividendes (brvm.org + sikafinance.com)
python scraper_news_brvm.py        # news
```

### Signaux

```bash
python generate_signals.py         # signaux + portefeuille
pytest                             # tests du moteur quant
```

### Base de données

```bash
createdb brvm
psql -d brvm -f database/schema.sql

export PGHOST=localhost PGDATABASE=brvm PGUSER=postgres PGPASSWORD=...
python database/import_data.py     # import complet (rejouable)
python database/link_news.py       # rapprochement news <-> actions
```

## Automatisation (GitHub Actions)

| Workflow | Rôle |
|---|---|
| `scrape-daily.yml` | Scraping quotidien (cours, dividendes, news) à 17h00 UTC, puis commit de `data/` |
| `generate-signals.yml` | Génération des signaux Prophet + Black-Litterman (tests en gate) |
| `daily_update.yml` | Régénération de la composition flottante après scraping |
| `refresh-powerbi.yml` | Déclenche l'actualisation du dataset Power BI après génération des signaux (voir [POWERBI.md](POWERBI.md)) |

Déclenchement manuel via *Actions → (workflow) → Run workflow*.

## Structure

```
.
├── data/
│   ├── actions/       # CSV historiques OHLCV
│   ├── indices/       # CSV + JSON des indices
│   ├── societes/      # JSON fiches sociétés
│   ├── fondamentaux/  # JSON fondamentaux 5 ans
│   ├── dividendes/    # CSV + JSON dividendes (+ avis PDF)
│   ├── news/          # CSV news
│   └── signals/       # signaux générés
├── scraper_*.py       # 5 scrapers
├── brvm_common.py     # helpers partagés
├── brvm_tickers.py    # tickers centralisés
├── brvm_emetteur_mapping.py
├── generate_signals.py / build_signals_history.py / build_composition_flottante.py
├── quant/             # moteur quantitatif
├── tests/             # tests du moteur quant
├── database/          # schéma PostgreSQL + scripts d'import et de liaison news
├── brvm_dashboard_enriched.html
└── .github/workflows/ # scrape-daily, generate-signals, daily_update
```
