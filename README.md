# Scraper BRVM

Pipeline de données BRVM (Bourse Régionale des Valeurs Mobilières) : scraping
quotidien des cours, sociétés, fondamentaux, dividendes et news, génération de
signaux quantitatifs, et dashboard HTML de visualisation.

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

### 3. Dashboard

`brvm_dashboard_enriched.html` — dashboard statique consolidant cours,
fondamentaux, dividendes et signaux à partir des fichiers de `data/`.

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

## Automatisation (GitHub Actions)

| Workflow | Rôle |
|---|---|
| `scrape-daily.yml` | Scraping quotidien (cours, dividendes, news) à 17h00 UTC, puis commit de `data/` |
| `generate-signals.yml` | Génération des signaux Prophet + Black-Litterman (tests en gate) |
| `daily_update.yml` | Régénération de la composition flottante après scraping |

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
├── brvm_dashboard_enriched.html
└── .github/workflows/ # scrape-daily, generate-signals, daily_update
```
