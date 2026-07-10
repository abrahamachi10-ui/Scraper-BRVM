"""Importe les jeux de donnees BRVM scrapes (dossier data/) dans la base
PostgreSQL creee par database/schema.sql.

Connexion (variables d'environnement standard psycopg2/libpq) :
    PGHOST      (defaut: localhost)
    PGPORT      (defaut: 5432)
    PGDATABASE  (defaut: brvm)
    PGUSER      (defaut: postgres)
    PGPASSWORD  (obligatoire)

Usage :
    python database/import_data.py
    python database/import_data.py --only actions,indices
"""

import argparse
import csv
import html
import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_WHITESPACE_RE = re.compile(r"[\s ]")

# Classification editoriale des indices (absente de la source, voir schema.sql)
INDEX_CATEGORY = {
    "BRVM30": ("Phare", "BRVM"),
    "BRVMC": ("Composite", "BRVM"),
    "BRVMPA": ("Compartiment", "BRVM"),
    "BRVMPR": ("Compartiment", "BRVM"),
    "BRVM-CB": ("Sectoriel", "BRVM"),
    "BRVM-CD": ("Sectoriel", "BRVM"),
    "BRVM-EN": ("Sectoriel", "BRVM"),
    "BRVM-IN": ("Sectoriel", "BRVM"),
    "BRVM-SF": ("Sectoriel", "BRVM"),
    "BRVM-TEL": ("Sectoriel", "BRVM"),
    "CAPIBRVM": ("Capitalisation", "BRVM"),
    "SIKAIDX": ("Tiers", "SikaFinance"),
    "SIKATR": ("Tiers", "SikaFinance"),
}


# ---------------------------------------------------------------------------
# Nettoyage
# ---------------------------------------------------------------------------

def clean_number(raw):
    """'8 377' / '28,97%' / '31 372 MFCFA' / '' / None -> float ou None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "n/a", "N/A"):
        return None
    s = _WHITESPACE_RE.sub("", s)
    s = s.replace("MFCFA", "").replace("%", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def clean_int(raw):
    val = clean_number(raw)
    return int(val) if val is not None else None


def clean_text(raw):
    return raw if raw not in (None, "") else None


# ---------------------------------------------------------------------------
# Lecture fichiers
# ---------------------------------------------------------------------------

def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def read_csv_rows(path, delimiter=";"):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


# ---------------------------------------------------------------------------
# Connexion / helpers DB
# ---------------------------------------------------------------------------

def connect():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "brvm"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
    )


def fetch_id_map(conn, table, key_col, id_col):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {key_col}, {id_col} FROM {table}")
        return dict(cur.fetchall())


# ---------------------------------------------------------------------------
# 1. actions  (source: data/societes/*_societe.json)
# ---------------------------------------------------------------------------

def import_actions(conn):
    rows = []
    for path in sorted((DATA_DIR / "societes").glob("*_societe.json")):
        d = read_json(path)
        ticker = d["ticker"]
        pays_code = ticker.rsplit(".", 1)[-1].lower()
        rows.append((
            ticker,
            clean_text(d.get("ISIN")),
            pays_code,
            d.get("Nom"),
            clean_text(d.get("Secteur")),
            clean_text(d.get("Adresse")),
            clean_text(d.get("Téléphone")),
            clean_text(d.get("Fax")),
            clean_text(d.get("Dirigeants")),
            clean_text(d.get("La société")),
            clean_int(d.get("Nombre_Actions")),
            clean_number(d.get("Flottant_Pct")),
            clean_number(d.get("Valorisation")),
        ))

    sql = """
        INSERT INTO actions (
            ticker, isin, pays_code, nom, secteur, adresse, telephone, fax,
            dirigeants, description, nombre_actions, flottant_pct, valorisation_mfcfa
        ) VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            isin = EXCLUDED.isin,
            pays_code = EXCLUDED.pays_code,
            nom = EXCLUDED.nom,
            secteur = EXCLUDED.secteur,
            adresse = EXCLUDED.adresse,
            telephone = EXCLUDED.telephone,
            fax = EXCLUDED.fax,
            dirigeants = EXCLUDED.dirigeants,
            description = EXCLUDED.description,
            nombre_actions = EXCLUDED.nombre_actions,
            flottant_pct = EXCLUDED.flottant_pct,
            valorisation_mfcfa = EXCLUDED.valorisation_mfcfa,
            updated_at = now()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()
    print(f"  actions : {len(rows)} lignes")


# ---------------------------------------------------------------------------
# 2. indices  (source: data/indices/*_info.json)
# ---------------------------------------------------------------------------

def import_indices(conn):
    rows = []
    for path in sorted((DATA_DIR / "indices").glob("*_info.json")):
        d = read_json(path)
        code = d["Symbol"]
        categorie, fournisseur = INDEX_CATEGORY.get(code, (None, "BRVM"))
        rows.append((code, d["Name"], clean_text(d.get("ISIN")), categorie, fournisseur))

    sql = """
        INSERT INTO indices (code, nom, isin, categorie, fournisseur)
        VALUES %s
        ON CONFLICT (code) DO UPDATE SET
            nom = EXCLUDED.nom,
            isin = EXCLUDED.isin,
            categorie = EXCLUDED.categorie,
            fournisseur = EXCLUDED.fournisseur,
            updated_at = now()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()
    print(f"  indices : {len(rows)} lignes")


# ---------------------------------------------------------------------------
# 3. historique_actions  (source: data/actions/*_historique.csv)
# ---------------------------------------------------------------------------

def _ticker_by_basename():
    """Associe 'ABJC_ci' (nom de fichier) -> 'ABJC.ci' (ticker canonique),
    en repartant du JSON societe correspondant plutot que de deviner via le
    nom de fichier (plus fiable, notamment pour les indices sectoriels)."""
    mapping = {}
    for path in (DATA_DIR / "societes").glob("*_societe.json"):
        base = path.name.replace("_societe.json", "")
        mapping[base] = read_json(path)["ticker"]
    return mapping


def import_historique_actions(conn):
    ticker_by_basename = _ticker_by_basename()
    action_id_by_ticker = fetch_id_map(conn, "actions", "ticker", "action_id")

    sql = """
        INSERT INTO historique_actions (
            action_id, date, ouverture, plus_haut, plus_bas, cloture,
            volume_titres, volume_fcfa, variation_pct
        ) VALUES %s
        ON CONFLICT (action_id, date) DO UPDATE SET
            ouverture = EXCLUDED.ouverture,
            plus_haut = EXCLUDED.plus_haut,
            plus_bas = EXCLUDED.plus_bas,
            cloture = EXCLUDED.cloture,
            volume_titres = EXCLUDED.volume_titres,
            volume_fcfa = EXCLUDED.volume_fcfa,
            variation_pct = EXCLUDED.variation_pct
    """

    total = 0
    for path in sorted((DATA_DIR / "actions").glob("*_historique.csv")):
        base = path.name.replace("_historique.csv", "")
        ticker = ticker_by_basename.get(base)
        action_id = action_id_by_ticker.get(ticker)
        if action_id is None:
            print(f"  [ignore] {path.name} : ticker introuvable dans actions")
            continue

        rows = [
            (
                action_id,
                row["Date"],
                clean_number(row["Ouverture"]),
                clean_number(row["Plus_Haut"]),
                clean_number(row["Plus_Bas"]),
                clean_number(row["Cloture"]),
                clean_int(row["Volume_Titres"]),
                clean_number(row["Volume_FCFA"]),
                clean_number(row["Variation_Pct"]),
            )
            for row in read_csv_rows(path)
        ]
        try:
            with conn.cursor() as cur:
                execute_values(cur, sql, rows, page_size=1000)
            conn.commit()
            total += len(rows)
        except Exception as exc:
            conn.rollback()
            print(f"  [erreur] {path.name} : {exc}")

    print(f"  historique_actions : {total} lignes")


# ---------------------------------------------------------------------------
# 4. historique_indices  (source: data/indices/*_historique.csv)
# ---------------------------------------------------------------------------

def _index_code_by_basename():
    mapping = {}
    for path in (DATA_DIR / "indices").glob("*_info.json"):
        base = path.name.replace("_info.json", "")
        mapping[base] = read_json(path)["Symbol"]
    return mapping


def import_historique_indices(conn):
    code_by_basename = _index_code_by_basename()
    indice_id_by_code = fetch_id_map(conn, "indices", "code", "indice_id")

    sql = """
        INSERT INTO historique_indices (
            indice_id, date, ouverture, plus_haut, plus_bas, cloture,
            volume_titres, volume_fcfa, variation_pct
        ) VALUES %s
        ON CONFLICT (indice_id, date) DO UPDATE SET
            ouverture = EXCLUDED.ouverture,
            plus_haut = EXCLUDED.plus_haut,
            plus_bas = EXCLUDED.plus_bas,
            cloture = EXCLUDED.cloture,
            volume_titres = EXCLUDED.volume_titres,
            volume_fcfa = EXCLUDED.volume_fcfa,
            variation_pct = EXCLUDED.variation_pct
    """

    total = 0
    for path in sorted((DATA_DIR / "indices").glob("*_historique.csv")):
        base = path.name.replace("_historique.csv", "")
        code = code_by_basename.get(base)
        indice_id = indice_id_by_code.get(code)
        if indice_id is None:
            print(f"  [ignore] {path.name} : code introuvable dans indices")
            continue

        rows = [
            (
                indice_id,
                row["Date"],
                clean_number(row["Ouverture"]),
                clean_number(row["Plus_Haut"]),
                clean_number(row["Plus_Bas"]),
                clean_number(row["Cloture"]),
                clean_int(row["Volume_Titres"]),
                clean_number(row["Volume_FCFA"]),
                clean_number(row["Variation_Pct"]),
            )
            for row in read_csv_rows(path)
        ]
        try:
            with conn.cursor() as cur:
                execute_values(cur, sql, rows, page_size=1000)
            conn.commit()
            total += len(rows)
        except Exception as exc:
            conn.rollback()
            print(f"  [erreur] {path.name} : {exc}")

    print(f"  historique_indices : {total} lignes")


# ---------------------------------------------------------------------------
# 5. dividendes  (source: data/dividendes/dividendes.csv -- fichier maitre uniquement)
# ---------------------------------------------------------------------------

def import_dividendes(conn):
    action_id_by_ticker = fetch_id_map(conn, "actions", "ticker", "action_id")
    path = DATA_DIR / "dividendes" / "dividendes.csv"

    rows = []
    skipped = 0
    for row in read_csv_rows(path):
        action_id = action_id_by_ticker.get(row["Ticker"])
        if action_id is None:
            skipped += 1
            continue
        rows.append((
            action_id,
            int(row["Exercice"]),
            row["Statut"],
            clean_text(row["Date_Detachement"]),
            clean_text(row["Date_Paiement"]),
            clean_number(row["Montant_Net_FCFA"]),
            clean_number(row["Rendement_Pct"]),
            clean_text(row["Avis_URL"]),
            clean_text(row["Avis_Path"]),
            clean_text(row["Sources"]),
            row["Date_Scraping"],
        ))

    sql = """
        INSERT INTO dividendes (
            action_id, exercice, statut, date_detachement, date_paiement,
            montant_net_fcfa, rendement_pct, avis_url, avis_path, sources, date_scraping
        ) VALUES %s
        ON CONFLICT (action_id, exercice) DO UPDATE SET
            statut = EXCLUDED.statut,
            date_detachement = EXCLUDED.date_detachement,
            date_paiement = EXCLUDED.date_paiement,
            montant_net_fcfa = EXCLUDED.montant_net_fcfa,
            rendement_pct = EXCLUDED.rendement_pct,
            avis_url = EXCLUDED.avis_url,
            avis_path = EXCLUDED.avis_path,
            sources = EXCLUDED.sources,
            date_scraping = EXCLUDED.date_scraping
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()
    print(f"  dividendes : {len(rows)} lignes ({skipped} ignorees, ticker inconnu)")


# ---------------------------------------------------------------------------
# 6. fondamentaux  (source: data/fondamentaux/*_fondamentaux.json)
# ---------------------------------------------------------------------------

def import_fondamentaux(conn):
    action_id_by_ticker = fetch_id_map(conn, "actions", "ticker", "action_id")

    sql = """
        INSERT INTO fondamentaux (
            action_id, exercice, chiffre_affaires_mfcfa, croissance_ca_pct,
            resultat_net_mfcfa, croissance_rn_pct, bnpa_fcfa, per, dividende_fcfa,
            source_url
        ) VALUES %s
        ON CONFLICT (action_id, exercice) DO UPDATE SET
            chiffre_affaires_mfcfa = EXCLUDED.chiffre_affaires_mfcfa,
            croissance_ca_pct = EXCLUDED.croissance_ca_pct,
            resultat_net_mfcfa = EXCLUDED.resultat_net_mfcfa,
            croissance_rn_pct = EXCLUDED.croissance_rn_pct,
            bnpa_fcfa = EXCLUDED.bnpa_fcfa,
            per = EXCLUDED.per,
            dividende_fcfa = EXCLUDED.dividende_fcfa,
            source_url = EXCLUDED.source_url
    """

    total = 0
    skipped = 0
    for path in sorted((DATA_DIR / "fondamentaux").glob("*_fondamentaux.json")):
        d = read_json(path)
        action_id = action_id_by_ticker.get(d["ticker"])
        if action_id is None:
            skipped += 1
            continue

        metrics = d.get("metrics", {})

        def metric(name, year):
            return clean_number(metrics.get(name, {}).get(year))

        rows = [
            (
                action_id,
                int(year),
                metric("Chiffre d'affaires", year),
                metric("Croissance CA", year),
                metric("Résultat net", year),
                metric("Croissance RN", year),
                metric("BNPA", year),
                metric("PER", year),
                metric("Dividende", year),
                d.get("source"),
            )
            for year in d.get("annees", [])
        ]
        try:
            with conn.cursor() as cur:
                execute_values(cur, sql, rows)
            conn.commit()
            total += len(rows)
        except Exception as exc:
            conn.rollback()
            print(f"  [erreur] {path.name} : {exc}")

    print(f"  fondamentaux : {total} lignes ({skipped} societes ignorees, ticker inconnu)")


# ---------------------------------------------------------------------------
# 7. news  (source: data/news/actualites_brvm.csv)
# ---------------------------------------------------------------------------

def import_news(conn):
    path = DATA_DIR / "news" / "actualites_brvm.csv"

    rows = []
    for row in read_csv_rows(path):
        rows.append((
            int(row["id"]),
            None,  # action_id : non renseigne dans la source, a completer via un rapprochement ulterieur
            None,  # indice_id : idem
            html.unescape(row["titre"]),
            html.unescape(row["date_publication"]),
            clean_text(html.unescape(row["auteur"])) if row["auteur"] else None,
            clean_text(row["categorie"]),
            clean_text(html.unescape(row["contenu"])) if row["contenu"] else None,
            clean_text(row["image_url"]),
            row["url"],
            row["date_scraping"],
        ))

    sql = """
        INSERT INTO news (
            source_id, action_id, indice_id, titre, date_publication, auteur,
            categorie, contenu, image_url, url, date_scraping
        ) VALUES %s
        ON CONFLICT (source_id) DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
    conn.commit()
    print(f"  news : {len(rows)} lignes proposees (doublons ignores via ON CONFLICT)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

ORDER = [
    "actions",
    "indices",
    "historique_actions",
    "historique_indices",
    "dividendes",
    "fondamentaux",
    "news",
]

STEPS = {
    "actions": import_actions,
    "indices": import_indices,
    "historique_actions": import_historique_actions,
    "historique_indices": import_historique_indices,
    "dividendes": import_dividendes,
    "fondamentaux": import_fondamentaux,
    "news": import_news,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        help="Liste de tables a importer, separees par des virgules (ex: actions,indices)",
    )
    args = parser.parse_args()

    steps = args.only.split(",") if args.only else ORDER

    conn = connect()
    try:
        for step in steps:
            print(f"--- {step} ---")
            STEPS[step](conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
