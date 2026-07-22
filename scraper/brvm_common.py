"""
Helpers communs aux scrapers BRVM
=================================
Factorise ce qui était dupliqué dans chaque scraper :

  - create_session() : session HTTP (User-Agent, en-têtes, warmup cookies, verify)
  - setup_logging()  : configuration logging (console + fichier optionnel)
  - parse_date_slash() / parse_date_fr() : conversion de dates vers ISO
  - parse_float()    : nettoyage de nombres ('1 740,00 FCFA' -> 1740.0)
  - clean_ws()       : normalisation des espaces

Chaque scraper conserve sa propre configuration (Accept, Referer, fichier log…)
en passant les paramètres adéquats ; le comportement reste identique.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_ACCEPT = "text/html,application/xhtml+xml"
DEFAULT_ACCEPT_LANGUAGE = "fr-FR,fr;q=0.9,en;q=0.8"


def create_session(
    *,
    accept: str = DEFAULT_ACCEPT,
    accept_language: str = DEFAULT_ACCEPT_LANGUAGE,
    referer: str | None = None,
    origin: str | None = None,
    extra_headers: dict | None = None,
    verify: bool = True,
    warmup_url: str | None = None,
    timeout: int = 30,
    logger: logging.Logger | None = None,
) -> requests.Session:
    """Crée une session requests configurée.

    warmup_url : si fourni, un GET initial est effectué pour amorcer les cookies.
    verify=False : désactive la vérification TLS (brvm.org, chaîne intermédiaire).
    """
    s = requests.Session()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": accept_language,
    }
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    if extra_headers:
        headers.update(extra_headers)
    s.headers.update(headers)
    s.verify = verify

    if warmup_url:
        try:
            s.get(warmup_url, timeout=timeout)
        except Exception as e:
            (logger or logging.getLogger(__name__)).warning(
                f"Impossible d'initialiser la session : {e}"
            )
    return s


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging(logfile: str | Path | None = None, *, name: str | None = None) -> logging.Logger:
    """Configure le logging racine (console, + fichier si `logfile`) et renvoie un logger."""
    handlers: list[logging.Handler] = []
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_MOIS_FR = {
    "janvier": "01", "fevrier": "02", "février": "02", "mars": "03",
    "avril": "04", "mai": "05", "juin": "06", "juillet": "07",
    "aout": "08", "août": "08", "septembre": "09", "octobre": "10",
    "novembre": "11", "decembre": "12", "décembre": "12",
}


def clean_ws(text: str) -> str:
    """Normalise les espaces (insécables compris) et trim."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_date_slash(txt: str) -> str:
    """'22/04/2026' -> '2026-04-22'. Sinon renvoie le texte trimé."""
    parts = (txt or "").strip().split("/")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return (txt or "").strip()


def parse_date_fr(txt: str) -> str:
    """'15 juin 2026' -> '2026-06-15'. Retourne '' si non parseable."""
    if not txt:
        return ""
    m = re.match(r"(\d{1,2})\s+([a-zûéèê]+)\s+(\d{4})", txt.strip().lower())
    if not m:
        return ""
    day, mois, year = m.groups()
    mm = _MOIS_FR.get(mois)
    return f"{year}-{mm}-{day.zfill(2)}" if mm else ""


def parse_float(txt):
    """'1 740,00' / '7,32 %' / '145,3214 FCFA' -> float. '-' ou vide -> None."""
    if txt is None:
        return None
    t = str(txt)
    for token in ("%", "FCFA", "\xa0", " "):
        t = t.replace(token, "")
    t = t.strip().replace(",", ".")
    if t in {"", "-"}:
        return None
    try:
        return float(t)
    except ValueError:
        return None
