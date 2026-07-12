"""
Scraper Dividendes BRVM - Unifié (brvm.org + sikafinance.com)
=============================================================
Source unique de vérité pour les dividendes BRVM. Consomme les deux
sources et produit UN SEUL jeu de fichiers fusionné.

Sources :
  - brvm.org  : paiements officiels (pagination complète), avec PDF d'avis
  - sikafinance.com : dividendes à venir + historique pluriannuel + rendements

Règles de fusion (BRVM = officiel, prioritaire), clé = (Ticker, Exercice) :
  - Date_Detachement / Date_Paiement : BRVM si date ISO valide, sinon Sika
  - Montant_Net_FCFA                : BRVM (warning si écart > 0.01 vs Sika)
  - Rendement_Pct                   : Sika uniquement (BRVM ne le publie pas)
  - Avis_URL / Avis_Path            : BRVM uniquement
  - Statut                          : recalculé après fusion (A venir / Passé / A préciser)

Sorties dans data/dividendes/ :
  - dividendes.csv          : tout (toutes années, tous statuts)
  - dividendes.json         : snapshot complet avec métadonnées
  - dividendes_a_venir.csv  : vue filtrée (Statut = 'A venir')
  - dividendes_passes.csv   : vue filtrée (Statut = 'Passé')
Les PDF d'avis BRVM sont téléchargés dans data/avis_dividendes/<TICKER>/.

Lance simplement : python scraper_dividendes.py
"""

from __future__ import annotations

import json
import re
import time
import urllib3
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from brvm_emetteur_mapping import lookup_ticker, lookup_brvm
from brvm_common import (
    create_session as _create_session,
    setup_logging,
    parse_date_slash as parse_date,
    parse_date_fr,
    parse_float,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIKA_BASE = "https://www.sikafinance.com"
SIKA_URL = SIKA_BASE + "/marches/dividendes"

BRVM_BASE = "https://www.brvm.org"
BRVM_URL = BRVM_BASE + "/fr/esv/paiement-de-dividendes"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "dividendes"
AVIS_DIR = PROJECT_ROOT / "data" / "avis_dividendes"

REQUEST_DELAY = 0.8   # politesse entre pages brvm.org
AVIS_DELAY = 0.5      # politesse entre téléchargements PDF
MAX_PAGES = 100       # garde-fou pagination
TIMEOUT = 30

# Anciens fichiers produits par les versions précédentes (mono-source) : nettoyés.
LEGACY_FILES = [
    "dividendes_historique.csv",
    "dividendes_brvm.csv",
    "dividendes_brvm.json",
]

log = setup_logging()

# Cache-buster d'en-têtes commun aux deux sources (contournement caches CDN)
_NO_CACHE = {"Cache-Control": "no-cache", "Pragma": "no-cache"}


def create_session(verify: bool = True) -> requests.Session:
    """Session requests partagée. verify=False pour brvm.org (chaîne de certif intermédiaire)."""
    return _create_session(extra_headers=_NO_CACHE, verify=verify, logger=log)


# ---------------------------------------------------------------------------
# Helpers de parsing
# ---------------------------------------------------------------------------

TICKER_RE = re.compile(r"cotation_([A-Za-z0-9\-]+\.[a-z]{2})", re.IGNORECASE)


def extract_ticker(a_tag) -> str:
    """Extrait le ticker depuis un href /marches/cotation_XXX.yy"""
    if not a_tag:
        return ""
    m = TICKER_RE.search(a_tag.get("href", ""))
    return m.group(1) if m else ""


def _is_iso(d) -> bool:
    return isinstance(d, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}$", d))


def _year_from_iso(date_iso: str) -> str:
    return date_iso[:4] if _is_iso(date_iso) else ""


# ---------------------------------------------------------------------------
# Scraping sikafinance.com
# ---------------------------------------------------------------------------


def fetch_sika_page(session: requests.Session) -> BeautifulSoup:
    # Cache-buster en query string pour contourner tout cache CDN
    url = f"{SIKA_URL}?_={int(time.time())}"
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def parse_sika_a_venir(soup: BeautifulSoup, today: datetime) -> list[dict]:
    """Table #tbdDiv : Date détachement | Nom | Montant | Rendement."""
    table = soup.find("table", id="tbdDiv")
    if not table:
        log.warning("[SIKA] Table 'tbdDiv' introuvable")
        return []

    data = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        nom_cell = tds[1]
        ticker = extract_ticker(nom_cell.find("a"))
        date_iso = parse_date(tds[0].get_text(strip=True))

        if _is_iso(date_iso):
            d = datetime.strptime(date_iso, "%Y-%m-%d")
            statut = "Passé" if d.date() < today.date() else "A venir"
        else:
            statut = "A préciser"

        data.append({
            "Date_Detachement": date_iso,
            "Statut": statut,
            "Ticker": ticker,
            "Nom_Canonique": lookup_ticker(ticker),
            "Nom": nom_cell.get_text(strip=True),
            "Source": "sikafinance.com",
            "Montant_Net_FCFA": parse_float(tds[2].get_text(strip=True)),
            "Rendement_Pct": parse_float(tds[3].get_text(strip=True)),
        })
    return data


def parse_sika_historique(soup: BeautifulSoup) -> list[dict]:
    """Table #tblDiv2 : Nom | Div. YYYY | Rend. YYYY | Div. YYYY+1 | ..."""
    table = soup.find("table", id="tblDiv2")
    if not table:
        log.warning("[SIKA] Table 'tblDiv2' introuvable")
        return []

    rows = table.find_all("tr")
    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]

    # Identifier les colonnes (Div. YYYY, Rend. YYYY)
    year_cols: list[tuple[int, int, str]] = []
    i = 1
    while i < len(headers):
        m_div = re.match(r"Div\.\s*(\d{4})", headers[i])
        if m_div:
            year = m_div.group(1)
            idx_rend = i + 1
            if idx_rend < len(headers) and re.match(rf"Rend\.\s*{year}", headers[idx_rend]):
                year_cols.append((i, idx_rend, year))
                i = idx_rend + 1
                continue
        i += 1

    data = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        nom_cell = tds[0]
        ticker = extract_ticker(nom_cell.find("a"))
        row = {
            "Ticker": ticker,
            "Nom_Canonique": lookup_ticker(ticker),
            "Nom": nom_cell.get_text(strip=True).replace("\xa0", " "),
            "Source": "sikafinance.com",
        }
        for idx_div, idx_rend, year in year_cols:
            div_txt = tds[idx_div].get_text(strip=True) if idx_div < len(tds) else ""
            rend_txt = tds[idx_rend].get_text(strip=True) if idx_rend < len(tds) else ""
            row[f"Div_{year}"] = parse_float(div_txt)
            row[f"Rend_{year}_Pct"] = parse_float(rend_txt)
        data.append(row)
    return data


# ---------------------------------------------------------------------------
# Scraping brvm.org
# ---------------------------------------------------------------------------


def fetch_brvm_page(session: requests.Session, page: int) -> BeautifulSoup:
    url = BRVM_URL if page == 0 else f"{BRVM_URL}?page={page}"
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return BeautifulSoup(resp.text, "lxml")


def find_dividend_table(soup: BeautifulSoup):
    """Identifie le tableau des dividendes par ses en-têtes."""
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any("emetteur" in h for h in headers) and any("dividende" in h for h in headers):
            return table
    return None


def detect_last_page(soup: BeautifulSoup) -> int:
    """Lit le pager pour connaître l'index de page max."""
    last = 0
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m:
            last = max(last, int(m.group(1)))
    return last


def parse_brvm_table(table) -> list[dict]:
    """Emetteur | Obligation | Action | Exercice | Date paiement | Date ex-div | Montant | Avis."""
    rows = table.find_all("tr")
    if not rows:
        return []
    out = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]
        if len(cells) < 7:
            continue

        # Lien PDF "Avis"
        avis_link = ""
        for td in tds:
            a = td.find("a", href=True)
            if a and "telecharger" in a.get_text(strip=True).lower():
                avis_link = urljoin(BRVM_BASE, a["href"])
                break
            if a and a["href"].lower().endswith(".pdf"):
                avis_link = urljoin(BRVM_BASE, a["href"])
                break

        emetteur = cells[0]
        date_exdiv_raw = cells[5]
        ticker, nom_canonique = lookup_brvm(emetteur)
        if not ticker and emetteur:
            log.warning(f"[BRVM] Emetteur sans ticker mappé : '{emetteur}'")

        out.append({
            "Ticker": ticker,
            "Nom_Canonique": nom_canonique,
            "Emetteur": emetteur,
            "Source": "brvm.org",
            "Obligation": cells[1],
            "Action": cells[2],
            "Exercice": cells[3],
            "Date_Paiement_Raw": cells[4],
            "Date_Paiement": parse_date_fr(cells[4]),
            # Alias homogène avec Sika
            "Date_Detachement": parse_date_fr(date_exdiv_raw),
            "Date_Ex_Dividende_Raw": date_exdiv_raw,
            "Date_Ex_Dividende": parse_date_fr(date_exdiv_raw),
            "Montant_Net_FCFA": parse_float(cells[6]),
            "Montant_Net_Raw": cells[6],
            "Avis_URL": avis_link,
        })
    return out


# ---------------------------------------------------------------------------
# Collecte des sources
# ---------------------------------------------------------------------------


def collect_brvm() -> list[dict]:
    """Récupère tous les paiements depuis brvm.org (pagination complète)."""
    session = create_session(verify=False)
    log.info("[BRVM] Scraping brvm.org/fr/esv/paiement-de-dividendes")
    soup = fetch_brvm_page(session, 0)
    last = min(detect_last_page(soup), MAX_PAGES)
    log.info(f"[BRVM] Dernière page : {last} ({last + 1} pages)")

    records: list[dict] = []
    seen: set[tuple] = set()
    for page in range(0, last + 1):
        if page > 0:
            time.sleep(REQUEST_DELAY)
            try:
                soup = fetch_brvm_page(session, page)
            except Exception as e:
                log.error(f"[BRVM] Page {page} : {e}")
                continue
        table = find_dividend_table(soup)
        if not table:
            log.warning(f"[BRVM] Page {page} : table introuvable")
            continue
        for r in parse_brvm_table(table):
            sig = (r["Ticker"], r["Exercice"], r["Date_Paiement"], r["Montant_Net_Raw"])
            if sig in seen:
                continue
            seen.add(sig)
            records.append(r)
    log.info(f"[BRVM] {len(records)} paiements collectés")
    return records


def collect_sika() -> tuple[list[dict], list[dict]]:
    """Récupère a_venir + historique depuis sikafinance.com."""
    session = create_session(verify=True)
    log.info("[SIKA] Scraping sikafinance.com/marches/dividendes")
    soup = fetch_sika_page(session)
    a_venir = parse_sika_a_venir(soup, datetime.now())
    historique = parse_sika_historique(soup)
    log.info(f"[SIKA] À venir : {len(a_venir)} | Historique : {len(historique)} sociétés")
    return a_venir, historique


# ---------------------------------------------------------------------------
# Normalisation vers le schéma unifié
# ---------------------------------------------------------------------------

_EXERCICE_FROM_PDF = re.compile(r"exercice[_\-]?(\d{4})", re.IGNORECASE)


def _clean_exercice(val) -> str:
    """Normalise l'exercice en string sans suffixe '.0'."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return ""
    return s[:-2] if s.endswith(".0") else s


def normalize_brvm(brvm_rows: list[dict]) -> list[dict]:
    """Convertit chaque ligne BRVM vers le schéma unifié."""
    out = []
    for r in brvm_rows:
        ticker = r.get("Ticker", "")
        if not ticker:
            continue  # lignes orphelines (Emetteur vide)
        exercice = _clean_exercice(r.get("Exercice"))
        # Fallback : inférer l'exercice depuis l'URL du PDF Avis
        if not exercice:
            m = _EXERCICE_FROM_PDF.search(r.get("Avis_URL", "") or "")
            if m:
                exercice = m.group(1)
        # Dernier recours : déduire de la Date_Paiement (année - 1)
        if not exercice:
            dp = r.get("Date_Paiement", "")
            if _is_iso(dp):
                exercice = str(int(dp[:4]) - 1)
        out.append({
            "Ticker": ticker,
            "Nom_Canonique": r.get("Nom_Canonique") or lookup_ticker(ticker),
            "Exercice": exercice,
            "Date_Detachement": r.get("Date_Detachement", "") or "",
            "Date_Paiement": r.get("Date_Paiement", "") or "",
            "Montant_Net_FCFA": r.get("Montant_Net_FCFA"),
            "Rendement_Pct": None,
            "Avis_URL": r.get("Avis_URL", "") or "",
            "_src": {"brvm.org"},
        })
    return out


def normalize_sika(a_venir: list[dict], historique: list[dict]) -> list[dict]:
    """Convertit Sika (a_venir + historique) vers le schéma unifié.

    Convention BRVM : un dividende détaché en année N correspond à l'exercice N-1.
    L'historique est éclaté en lignes (Ticker, Exercice).
    """
    out: list[dict] = []

    for r in a_venir:
        ticker = r.get("Ticker", "")
        if not ticker:
            continue
        date_det = r.get("Date_Detachement", "")
        year_det = _year_from_iso(date_det)
        exercice = str(int(year_det) - 1) if year_det else str(datetime.now().year - 1)
        out.append({
            "Ticker": ticker,
            "Nom_Canonique": r.get("Nom_Canonique") or lookup_ticker(ticker),
            "Exercice": exercice,
            "Date_Detachement": date_det if _is_iso(date_det) else "",
            "Date_Paiement": "",
            "Montant_Net_FCFA": r.get("Montant_Net_FCFA"),
            "Rendement_Pct": r.get("Rendement_Pct"),
            "Avis_URL": "",
            "_src": {"sikafinance.com"},
        })

    for r in historique:
        ticker = r.get("Ticker", "")
        if not ticker:
            continue
        for key, val in r.items():
            m = re.match(r"^Div_(\d{4})$", key)
            if not m or val in (None, "", "nan"):
                continue
            try:
                if pd.isna(val):
                    continue
            except Exception:
                pass
            year = m.group(1)
            out.append({
                "Ticker": ticker,
                "Nom_Canonique": r.get("Nom_Canonique") or lookup_ticker(ticker),
                "Exercice": year,
                "Date_Detachement": "",
                "Date_Paiement": "",
                "Montant_Net_FCFA": val,
                "Rendement_Pct": r.get(f"Rend_{year}_Pct"),
                "Avis_URL": "",
                "_src": {"sikafinance.com"},
            })
    return out


# ---------------------------------------------------------------------------
# Fusion BRVM > Sika
# ---------------------------------------------------------------------------


def _coalesce_date(brvm_val: str, sika_val: str) -> str:
    if _is_iso(brvm_val):
        return brvm_val
    if _is_iso(sika_val):
        return sika_val
    return ""


def _coalesce_montant(brvm_val, sika_val, ticker: str, exercice: str) -> float | None:
    """BRVM gagne. Log warning si écart > 0.01 FCFA avec Sika."""
    def _ok(v):
        return v is not None and not (isinstance(v, float) and pd.isna(v))

    if _ok(brvm_val):
        if _ok(sika_val):
            try:
                if abs(float(brvm_val) - float(sika_val)) > 0.01:
                    log.warning(
                        f"[FUSION] Montant divergent {ticker} exercice {exercice} : "
                        f"BRVM={brvm_val} vs Sika={sika_val} → BRVM gagne"
                    )
            except (TypeError, ValueError):
                pass
        return float(brvm_val)
    if _ok(sika_val):
        return float(sika_val)
    return None


def merge(rows: list[dict]) -> list[dict]:
    """Fusionne les lignes par clé (Ticker, Exercice). BRVM prioritaire."""
    bucket: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["Ticker"], r["Exercice"])
        if key not in bucket:
            bucket[key] = {**r, "_src": set(r["_src"])}
            continue
        cur = bucket[key]
        cur_is_brvm = "brvm.org" in cur["_src"]
        new_is_brvm = "brvm.org" in r["_src"]
        brvm_row = cur if cur_is_brvm else (r if new_is_brvm else {})
        sika_row = cur if not cur_is_brvm else (r if not new_is_brvm else {})

        bucket[key] = {
            "Ticker": cur["Ticker"],
            "Exercice": cur["Exercice"],
            "Nom_Canonique": cur.get("Nom_Canonique") or r.get("Nom_Canonique"),
            "Date_Detachement": _coalesce_date(
                brvm_row.get("Date_Detachement", ""), sika_row.get("Date_Detachement", "")),
            "Date_Paiement": _coalesce_date(
                brvm_row.get("Date_Paiement", ""), sika_row.get("Date_Paiement", "")),
            "Montant_Net_FCFA": _coalesce_montant(
                brvm_row.get("Montant_Net_FCFA"), sika_row.get("Montant_Net_FCFA"),
                cur["Ticker"], cur["Exercice"]),
            "Rendement_Pct": (
                sika_row.get("Rendement_Pct") if sika_row else cur.get("Rendement_Pct")),
            "Avis_URL": brvm_row.get("Avis_URL", "") or cur.get("Avis_URL", ""),
            "_src": cur["_src"] | r["_src"],
        }
    return list(bucket.values())


# ---------------------------------------------------------------------------
# Avis PDF
# ---------------------------------------------------------------------------


def _avis_local_path(ticker: str, exercice: str, avis_url: str) -> Path:
    """data/avis_dividendes/<TICKER>/<EXERCICE>_<basename>.pdf"""
    basename = avis_url.rsplit("/", 1)[-1] or "avis.pdf"
    if not basename.lower().endswith(".pdf"):
        basename += ".pdf"
    prefix = f"{exercice}_" if exercice else ""
    return AVIS_DIR / ticker / f"{prefix}{basename}"


def download_avis(records: list[dict]) -> dict[tuple[str, str], str]:
    """Télécharge les PDF d'avis BRVM (skip si déjà présent).

    Retourne un mapping (Ticker, Exercice) -> chemin local relatif au projet.
    """
    AVIS_DIR.mkdir(parents=True, exist_ok=True)
    session = create_session(verify=False)

    paths: dict[tuple[str, str], str] = {}
    n_download = n_skip = n_fail = 0

    for r in records:
        url = r.get("Avis_URL", "")
        ticker = r.get("Ticker", "")
        if not url or not ticker:
            continue
        exercice = r.get("Exercice", "")
        path = _avis_local_path(ticker, exercice, url)
        rel = path.relative_to(PROJECT_ROOT).as_posix()

        if path.exists() and path.stat().st_size > 0:
            paths[(ticker, exercice)] = rel
            n_skip += 1
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=16384):
                    if chunk:
                        f.write(chunk)
            if path.stat().st_size == 0:
                raise RuntimeError("fichier vide")
            paths[(ticker, exercice)] = rel
            n_download += 1
            log.info(f"[AVIS] {ticker} {exercice} -> {rel}")
        except Exception as e:
            log.warning(f"[AVIS] échec {ticker} {exercice} ({url}) : {e}")
            n_fail += 1
            if path.exists() and path.stat().st_size == 0:
                path.unlink()
        finally:
            time.sleep(AVIS_DELAY)

    log.info(f"[AVIS] téléchargés : {n_download} | cache : {n_skip} | échecs : {n_fail}")
    return paths


# ---------------------------------------------------------------------------
# Statut & sortie
# ---------------------------------------------------------------------------


def compute_statut(row: dict, today: datetime) -> str:
    d = row.get("Date_Detachement", "")
    if not _is_iso(d):
        return "A préciser"
    dt = datetime.strptime(d, "%Y-%m-%d").date()
    return "Passé" if dt < today.date() else "A venir"


def to_dataframe(records: list[dict], now: datetime) -> pd.DataFrame:
    rows = [{
        "Ticker": r["Ticker"],
        "Nom_Canonique": r.get("Nom_Canonique", ""),
        "Exercice": r["Exercice"],
        "Statut": compute_statut(r, now),
        "Date_Detachement": r.get("Date_Detachement", ""),
        "Date_Paiement": r.get("Date_Paiement", ""),
        "Montant_Net_FCFA": r.get("Montant_Net_FCFA"),
        "Rendement_Pct": r.get("Rendement_Pct"),
        "Avis_URL": r.get("Avis_URL", ""),
        "Avis_Path": r.get("Avis_Path", ""),
        "Sources": ",".join(sorted(r.get("_src", set()))),
    } for r in records]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(
        by=["Exercice", "Date_Paiement", "Ticker"],
        ascending=[False, False, True],
        na_position="last",
    ).reset_index(drop=True)
    df.insert(0, "Date_Scraping", now.strftime("%Y-%m-%d"))
    return df


def cleanup_legacy():
    for name in LEGACY_FILES:
        path = OUTPUT_DIR / name
        if path.exists():
            path.unlink()
            log.info(f"[CLEANUP] supprimé : {path.name}")


def save(df: pd.DataFrame, now: datetime):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_legacy()

    csv_path = OUTPUT_DIR / "dividendes.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    log.info(f"-> {csv_path} ({len(df)} lignes)")

    avenir = df[df["Statut"] == "A venir"]
    passes = df[df["Statut"] == "Passé"]
    apreciser = df[df["Statut"] == "A préciser"]

    p1 = OUTPUT_DIR / "dividendes_a_venir.csv"
    avenir.to_csv(p1, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    log.info(f"-> {p1} ({len(avenir)} à venir)")

    p2 = OUTPUT_DIR / "dividendes_passes.csv"
    passes.to_csv(p2, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    log.info(f"-> {p2} ({len(passes)} passés)")

    snapshot = {
        "date_scraping": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": ["brvm.org", "sikafinance.com"],
        "nb_records": len(df),
        "nb_a_venir": len(avenir),
        "nb_passes": len(passes),
        "nb_a_preciser": len(apreciser),
        "records": df.where(pd.notna(df), None).to_dict(orient="records"),
    }
    json_path = OUTPUT_DIR / "dividendes.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"-> {json_path}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run():
    start = time.time()
    now = datetime.now()

    brvm_raw = collect_brvm()
    sika_avenir, sika_hist = collect_sika()

    brvm_norm = normalize_brvm(brvm_raw)
    sika_norm = normalize_sika(sika_avenir, sika_hist)
    log.info(f"[NORMALISE] BRVM={len(brvm_norm)} | Sika={len(sika_norm)}")

    merged = merge(brvm_norm + sika_norm)
    log.info(f"[FUSION] {len(merged)} lignes uniques (Ticker, Exercice)")

    avis_paths = download_avis(merged)
    for r in merged:
        r["Avis_Path"] = avis_paths.get((r["Ticker"], r["Exercice"]), "")

    df = to_dataframe(merged, now)
    if not df.empty:
        log.info(
            f"[STATUT] À venir : {(df['Statut'] == 'A venir').sum()} | "
            f"Passés : {(df['Statut'] == 'Passé').sum()} | "
            f"À préciser : {(df['Statut'] == 'A préciser').sum()}"
        )

    save(df, now)
    log.info(f"Terminé en {time.time() - start:.1f}s")


if __name__ == "__main__":
    run()
