"""
Scraper Dividendes - Site officiel BRVM
=======================================
Source : https://www.brvm.org/fr/esv/paiement-de-dividendes

Parcourt l'ensemble des pages (pagination ?page=N) et agrège le tableau
des paiements de dividendes (Emetteur, Action/Obligation, Exercice,
Date de paiement, Date ex-dividende, Montant net, lien Avis PDF).

Sortie : data/dividendes/
  - dividendes_brvm.csv
  - dividendes_brvm.json
"""

import json
import logging
import re
import time
import urllib3
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from brvm_emetteur_mapping import lookup_brvm

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.brvm.org"
DIV_URL = BASE_URL + "/fr/esv/paiement-de-dividendes"

OUTPUT_DIR = Path(__file__).parent / "data" / "dividendes"

REQUEST_DELAY = 0.8  # politesse
MAX_PAGES = 100      # garde-fou
TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


MOIS_FR = {
    "janvier": "01", "fevrier": "02", "février": "02", "mars": "03",
    "avril": "04", "mai": "05", "juin": "06", "juillet": "07",
    "aout": "08", "août": "08", "septembre": "09", "octobre": "10",
    "novembre": "11", "decembre": "12", "décembre": "12",
}


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    s.verify = False
    return s


def parse_date_fr(txt: str) -> str:
    """'15 juin 2026' -> '2026-06-15'. Retourne '' si non parseable."""
    if not txt:
        return ""
    t = txt.strip().lower()
    m = re.match(r"(\d{1,2})\s+([a-zûéèê]+)\s+(\d{4})", t)
    if not m:
        return ""
    day, mois, year = m.groups()
    mm = MOIS_FR.get(mois)
    if not mm:
        return ""
    return f"{year}-{mm}-{day.zfill(2)}"


def clean_montant(txt: str):
    """'145,3214 FCFA' -> 145.3214. '-' -> None."""
    if not txt:
        return None
    t = txt.replace("FCFA", "").replace("\xa0", " ").strip()
    if t in {"", "-"}:
        return None
    t = t.replace(" ", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def fetch_page(session: requests.Session, page: int) -> BeautifulSoup:
    url = DIV_URL if page == 0 else f"{DIV_URL}?page={page}"
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    # Le site renvoie de l'UTF-8 mais quelques caractères mal encodés apparaissent ;
    # on force le décodage UTF-8.
    resp.encoding = "utf-8"
    return BeautifulSoup(resp.text, "lxml")


def find_dividend_table(soup: BeautifulSoup):
    """Identifie le tableau des dividendes par ses en-têtes."""
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any("emetteur" in h for h in headers) and any("dividende" in h for h in headers):
            return table
    return None


def parse_table(table) -> list[dict]:
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    out = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        # Cas standard : 9 colonnes
        # Emetteur | Obligation | Action | Exercice | Date paiement | Date ex-div | Montant | Avis | (vide)
        if len(cells) < 7:
            continue

        # Récupération du lien PDF "Avis"
        avis_link = ""
        for td in tds:
            a = td.find("a", href=True)
            if a and "telecharger" in a.get_text(strip=True).lower():
                avis_link = urljoin(BASE_URL, a["href"])
                break
            if a and a["href"].lower().endswith(".pdf"):
                avis_link = urljoin(BASE_URL, a["href"])
                break

        emetteur = cells[0]
        obligation = cells[1]
        action = cells[2]
        exercice = cells[3]
        date_paiement_raw = cells[4]
        date_exdiv_raw = cells[5]
        montant_raw = cells[6]

        ticker, nom_canonique = lookup_brvm(emetteur)
        if not ticker and emetteur:
            log.warning(f"Emetteur sans ticker mappé : '{emetteur}'")

        out.append({
            "Ticker": ticker,
            "Nom_Canonique": nom_canonique,
            "Emetteur": emetteur,
            "Source": "brvm.org",
            "Obligation": obligation,
            "Action": action,
            "Exercice": exercice,
            "Date_Paiement_Raw": date_paiement_raw,
            "Date_Paiement": parse_date_fr(date_paiement_raw),
            # Alias 'Date_Detachement' = même concept que 'Date_Ex_Dividende',
            # exposé pour homogénéité avec scraper_dividendes.py (Sikafinance).
            "Date_Detachement": parse_date_fr(date_exdiv_raw),
            "Date_Ex_Dividende_Raw": date_exdiv_raw,
            "Date_Ex_Dividende": parse_date_fr(date_exdiv_raw),
            "Montant_Net_FCFA": clean_montant(montant_raw),
            "Montant_Net_Raw": montant_raw,
            "Avis_URL": avis_link,
        })
    return out


def detect_last_page(soup: BeautifulSoup) -> int:
    """Lit le lien 'dernier' du pager pour connaître l'index max."""
    last = 0
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m:
            last = max(last, int(m.group(1)))
    return last


def save(records: list[dict], now: datetime):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")

    df = pd.DataFrame(records)
    if not df.empty:
        df.insert(0, "Date_Scraping", stamp)
        # Tri par date de paiement décroissante (les dates ISO vides en bas)
        df = df.sort_values(
            by=["Date_Paiement", "Emetteur"],
            ascending=[False, True],
            na_position="last",
        )

    csv_path = OUTPUT_DIR / "dividendes_brvm.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    log.info(f"-> {csv_path} ({len(df)} lignes)")

    json_path = OUTPUT_DIR / "dividendes_brvm.json"
    snapshot = {
        "date_scraping": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": DIV_URL,
        "nb_records": len(records),
        "records": records,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    log.info(f"-> {json_path}")


def run():
    session = create_session()
    now = datetime.now()
    log.info(f"Scraping {DIV_URL}")

    # 1ère page pour découvrir la pagination
    soup = fetch_page(session, 0)
    last_page = detect_last_page(soup)
    log.info(f"Dernière page détectée : {last_page} (soit {last_page + 1} pages)")
    if last_page > MAX_PAGES:
        log.warning(f"Pagination > MAX_PAGES={MAX_PAGES}, limitation appliquée")
        last_page = MAX_PAGES

    all_records: list[dict] = []
    seen_signatures: set[tuple] = set()  # déduplication

    for page in range(0, last_page + 1):
        if page > 0:
            time.sleep(REQUEST_DELAY)
            try:
                soup = fetch_page(session, page)
            except Exception as e:
                log.error(f"Page {page} : erreur {e}")
                continue

        table = find_dividend_table(soup)
        if not table:
            log.warning(f"Page {page} : table introuvable")
            continue

        rows = parse_table(table)
        added = 0
        for r in rows:
            sig = (
                r["Emetteur"], r["Exercice"],
                r["Date_Paiement"], r["Montant_Net_Raw"],
            )
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            all_records.append(r)
            added += 1
        log.info(f"Page {page:>2} : {len(rows)} lignes lues, {added} ajoutées (total {len(all_records)})")

    save(all_records, now)


if __name__ == "__main__":
    start = time.time()
    run()
    log.info(f"Terminé en {time.time() - start:.1f}s")
