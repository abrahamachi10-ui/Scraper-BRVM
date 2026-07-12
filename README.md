# Scraper BRVM

Pipeline de données BRVM (Bourse Régionale des Valeurs Mobilières) : scraping
quotidien des cours, sociétés, fondamentaux, dividendes et news, génération de
signaux quantitatifs, et dashboard HTML de visualisation.

## Composants

### 1. Scrapers (`scraper/`)

| Script | Source | Fonction | Sortie |
|---|---|---|---|
| `scraper_brvm.py` | sikafinance.com | Historique OHLCV des actions et indices | `data/actions/*.csv`, `data/indices/*.csv` |
| `scraper_info_societes.py` | sikafinance.com | Fiches sociétés (capi, flottant, secteur, etc.) | `data/societes/*.json`, `data/indices/*_info.json` |
| `scraper_fondamentaux.py` | sikafinance.com | Fondamentaux 5 ans (CA, RN, BNPA, PER, dividende) | `data/fondamentaux/*.json` |
| `scraper_dividendes.py` | brvm.org + sikafinance.com | Dividendes (à venir/passés) fusionnés + avis PDF | `data/dividendes/*.csv`, `*.json` |
| `scraper_news_brvm.py` | sikafinance.com | News financières BRVM | `data/news/*.csv` |

Modules partagés (aussi dans `scraper/`) :

| Module | Rôle |
|---|---|
| `brvm_tickers.py` | Liste centralisée des tickers (48 actions, 13 indices) |
| `brvm_emetteur_mapping.py` | Mapping émetteur BRVM ↔ ticker ↔ nom canonique |
| `brvm_common.py` | Helpers HTTP/logging/parsing communs aux scrapers |

### 2. Signaux & composition (`pipeline/`)

| Script | Fonction | Sortie |
|---|---|---|
| `generate_signals.py` | Signaux Prophet + Black-Litterman (+ backtest) | `data/signals/*` |
| `build_signals_history.py` | Historique pluriannuel des signaux | `data/signals/*` |
| `build_composition_flottante.py` | Composition flottante des indices | `Composition_flottante.xlsx` |

Le moteur quantitatif est dans `quant/` (backtest, covariance, portfolio,
signals), couvert par la suite de tests `tests/`.

### 3. Base de données (`database/`)

Import des données scrapées dans PostgreSQL et liaison des news aux tickers
(`import_data.py`, `link_news.py`, `schema.sql`).

### 4. Dashboards (`dashboard/`)

- `brvm_dashboard_enriched.html` — dashboard statique consolidant cours,
  fondamentaux, dividendes et signaux à partir des fichiers de `data/`.
- Rapport Power BI multi-pages (Vue d'ensemble, Détail action, Détail indice)
  connecté au dépôt GitHub et actualisé automatiquement. Voir
  [POWERBI_SPEC.md](docs/POWERBI_SPEC.md).

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

### Scrapers (manuels)

```bash
python scraper/scraper_brvm.py             # cours actions + indices
python scraper/scraper_info_societes.py    # fiches sociétés
python scraper/scraper_fondamentaux.py     # fondamentaux 5 ans
python scraper/scraper_dividendes.py       # dividendes (brvm.org + sikafinance.com)
python scraper/scraper_news_brvm.py        # news
```

### Signaux

```bash
python pipeline/generate_signals.py         # signaux + portefeuille
pytest                                       # tests du moteur quant
```

## Automatisation (GitHub Actions)

| Workflow | Rôle |
|---|---|
| `scrape-daily.yml` | Scraping quotidien (cours, dividendes, news) à 17h00 UTC, puis commit de `data/` |
| `generate-signals.yml` | Génération des signaux Prophet + Black-Litterman (tests en gate) |
| `daily_update.yml` | Régénération de la composition flottante après scraping |
| `refresh-powerbi.yml` | Déclenche l'actualisation du dataset Power BI après génération des signaux (voir [POWERBI_SPEC.md](docs/POWERBI_SPEC.md)) |

Déclenchement manuel via *Actions → (workflow) → Run workflow*.

## Structure

```
.
├── data/               # données scrapées + générées (déjà organisées)
│   ├── actions/        # CSV historiques OHLCV
│   ├── indices/        # CSV + JSON des indices
│   ├── societes/       # JSON fiches sociétés
│   ├── fondamentaux/   # JSON fondamentaux 5 ans
│   ├── dividendes/     # CSV + JSON dividendes (+ avis PDF)
│   ├── news/           # CSV news
│   ├── signals/        # signaux générés
│   └── algo/           # portefeuille généré
├── scraper/            # 5 scrapers + modules partagés (brvm_common, brvm_tickers, brvm_emetteur_mapping)
├── pipeline/           # generate_signals.py, build_signals_history.py, build_composition_flottante.py
├── quant/              # moteur quantitatif (backtest, covariance, portfolio, signals)
├── database/           # import PostgreSQL + liaison news (import_data.py, link_news.py, schema.sql)
├── dashboard/          # brvm_dashboard_enriched.html
├── docs/               # POWERBI_SPEC.md
├── tests/              # tests du moteur quant
├── simulateur-bourse/  # app Next.js (jeu de simulation boursière)
└── .github/workflows/  # scrape-daily, generate-signals, daily_update, refresh-powerbi
```
