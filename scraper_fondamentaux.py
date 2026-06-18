"""
Scraper Données Fondamentales Sociétés BRVM - Sikafinance.com
=============================================================
Récupère la matrice historique 5 ans des indicateurs fondamentaux affichée
sur la fiche société Sikafinance (ex: https://www.sikafinance.com/marches/societe/NSBC.ci) :

  - Chiffre d'affaires
  - Croissance CA
  - Résultat net
  - Croissance RN
  - BNPA
  - PER
  - Dividende

Sortie :
  data/fondamentaux/{TICKER}_fondamentaux.json

Utilisation :
  python scraper_fondamentaux.py                 # Toutes les actions
  python scraper_fondamentaux.py test [TICKER]   # Un seul ticker (défaut NSBC.ci)
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from brvm_tickers import ACTIONS, safe_filename
from brvm_common import (
    create_session as _create_session,
    setup_logging,
    clean_ws as _clean,
)

BASE_URL = "https://www.sikafinance.com"
SOCIETE_URL = BASE_URL + "/marches/societe/{ticker}"
REQUEST_DELAY = 0.5

OUTPUT_DIR = Path(__file__).parent / "data" / "fondamentaux"

log = setup_logging(Path(__file__).parent / "scraper_fondamentaux.log")

EXPECTED_METRICS = {
    "Chiffre d'affaires",
    "Croissance CA",
    "Résultat net",
    "Croissance RN",
    "BNPA",
    "PER",
    "Dividende",
}


def create_session() -> requests.Session:
    return _create_session(referer=BASE_URL, warmup_url=BASE_URL, logger=log)


def _find_fondamentaux_table(soup: BeautifulSoup):
    """Trouve le tableau dont la 1ère ligne est une suite d'années (4 chiffres)."""
    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if not first_tr:
            continue
        cells = [_clean(td.get_text()) for td in first_tr.find_all(["td", "th"])]
        years = [c for c in cells if re.fullmatch(r"\d{4}", c)]
        if len(years) >= 2:
            metrics = {
                _clean(tr.find(["td", "th"]).get_text())
                for tr in table.find_all("tr")[1:]
                if tr.find(["td", "th"])
            }
            if metrics & EXPECTED_METRICS:
                return table
    return None


def scrape_fondamentaux(session: requests.Session, ticker: str) -> dict:
    url = SOCIETE_URL.format(ticker=ticker)
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"  HTTP {ticker}: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "lxml")
    table = _find_fondamentaux_table(soup)
    if not table:
        log.warning(f"  Tableau fondamentaux introuvable pour {ticker}")
        return {}

    rows = table.find_all("tr")
    header_cells = [_clean(td.get_text()) for td in rows[0].find_all(["td", "th"])]
    years = [c for c in header_cells if re.fullmatch(r"\d{4}", c)]

    metrics: dict[str, dict[str, str]] = {}
    for tr in rows[1:]:
        cells = [_clean(td.get_text()) for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label = cells[0]
        values = cells[1:]
        if not label:
            continue
        per_year: dict[str, str] = {}
        for i, year in enumerate(years):
            v = values[i] if i < len(values) else ""
            if v and v != "-":
                per_year[year] = v
        metrics[label] = per_year

    return {
        "ticker": ticker,
        "source": url,
        "annees": years,
        "metrics": metrics,
    }


def save(data: dict, ticker: str) -> None:
    if not data or not data.get("metrics"):
        log.warning(f"  Rien à sauver pour {ticker}")
        return
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{safe_filename(ticker)}_fondamentaux.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"  -> {path}")


def run_all() -> None:
    session = create_session()
    log.info("=" * 60)
    log.info(f"FONDAMENTAUX — {len(ACTIONS)} actions")
    log.info("=" * 60)
    start = time.time()
    for i, ticker in enumerate(ACTIONS, 1):
        log.info(f"[{i}/{len(ACTIONS)}] {ticker}")
        try:
            data = scrape_fondamentaux(session, ticker)
            save(data, ticker)
        except Exception as e:
            log.error(f"  ERREUR {ticker}: {e}")
        time.sleep(REQUEST_DELAY)
    log.info(f"Terminé en {(time.time()-start)/60:.1f} min")


def run_test(ticker: str) -> None:
    session = create_session()
    log.info(f"=== TEST: {ticker} ===")
    data = scrape_fondamentaux(session, ticker)
    save(data, ticker)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        run_test(sys.argv[2] if len(sys.argv) > 2 else "NSBC.ci")
    else:
        run_all()
