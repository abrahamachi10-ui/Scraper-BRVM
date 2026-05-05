# Scraper BRVM + ETF Builder

Pipeline de données BRVM (Bourse Régionale des Valeurs Mobilières) + app Streamlit
de construction d'ETF répliquant un indice BRVM.

## Composants

### 1. Scrapers (source : sikafinance.com)

| Script | Fonction | Sortie |
|---|---|---|
| `scraper_brvm.py` | Historique OHLCV des actions et indices | `data/actions/*.csv`, `data/indices/*.csv` |
| `scraper_info_societes.py` | Fiches sociétés (capi, flottant, BNPA, etc.) | `data/societes/*.json`, `data/indices/*_info.json` |
| `scraper_dividendes.py` | Dividendes à venir + historique pluriannuel | `data/dividendes/*.csv`, `*.json` |
| `scraper_news_brvm.py` | News financières BRVM | `data/news/*.csv` |
| `brvm_tickers.py` | Liste centralisée des tickers (47 actions, 13 indices) | — |

### 2. ETF Builder (Streamlit)

`etf_builder.py` — app pour construire un ETF répliquant un indice BRVM
(BRVM30 par défaut) à partir des données scrappées.

Fonctionnalités :
- 6 méthodes de pondération : équipondéré, capi, free-float, inverse-vol,
  tracking-error min, Sharpe max
- Filtres de liquidité paramétrables (volume, ADV, capi, flottant, etc.)
- Backtest avec rebalancement périodique (mensuel → annuel) et drift
- Onglet Grid Search pour explorer la grille de paramètres
- Onglet Documentation avec formules

### 3. Notebook

`etf_builder_notebook.ipynb` — reprend la logique de l'app sans Streamlit
pour expérimenter en Python pur.

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

### Scrapers (manuels)

```bash
python scraper_brvm.py             # cours actions + indices
python scraper_info_societes.py    # fiches sociétés
python scraper_dividendes.py       # dividendes
python scraper_news_brvm.py        # news
```

### App Streamlit

```bash
streamlit run etf_builder.py
```

## Automatisation (GitHub Actions)

Le workflow `.github/workflows/scrape-daily.yml` exécute les 4 scrapers
automatiquement chaque jour ouvré à **17h00 UTC** (clôture BRVM ~15h UTC),
puis commit les données mises à jour dans `data/`.

Déclenchement manuel possible via *Actions → Scraping BRVM quotidien → Run workflow*.

## Structure

```
.
├── data/
│   ├── actions/       # CSV historiques OHLCV
│   ├── indices/       # CSV + JSON des indices
│   ├── societes/      # JSON fiches sociétés
│   ├── dividendes/    # CSV + JSON dividendes
│   └── news/          # CSV news
├── scraper_*.py       # 4 scrapers
├── brvm_tickers.py    # tickers centralisés
├── etf_builder.py     # app Streamlit
├── etf_builder_notebook.ipynb
└── .github/workflows/scrape-daily.yml
```
