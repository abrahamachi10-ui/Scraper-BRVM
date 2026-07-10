"""Rapproche automatiquement les articles de `news` avec une action (`action_id`)
et/ou un indice (`indice_id`), par recherche de mots-cles dans le titre.

C'est une heuristique, pas une verite garantie : un article n'est lie que si
EXACTEMENT UNE societe (ou UN indice) correspond a son titre. Si plusieurs
societes sont citees (ex: recap hebdomadaire "BRVM / Semaine 20"), l'article
reste volontairement non lie plutot que de deviner.

Les motifs par ticker incluent un qualificatif pays quand la marque existe
dans plusieurs pays (Orange, TotalEnergies, Societe Generale, Bank of Africa,
CFAO, Nestle, Coris Bank, Unilever...) pour eviter les faux positifs deja
observes dans ce jeu de donnees (ex: "Orange Tunisie", "TotalEnergies EP"
Gabon, "Coris Bank International Tchad" ne concernent pas les tickers BRVM).

Usage:
    python database/link_news.py            # applique les mises a jour
    python database/link_news.py --dry-run   # affiche les stats sans ecrire
"""

import argparse
import csv
import os
import re
import unicodedata

import psycopg2
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Motifs de recherche par ticker (voir docstring pour la logique de calibrage)
# ---------------------------------------------------------------------------

ACTION_PATTERNS = {
    "ABJC.ci": ["servair abidjan", "servair ci"],
    "BICB.bj": ["bicib", "banque internationale pour le commerce du benin"],
    "BICC.ci": ["bicici"],
    "BNBC.ci": ["bernabe"],
    "BOAB.bj": ["boa benin", "bank of africa benin"],
    "BOABF.bf": ["boa burkina", "bank of africa burkina"],
    "BOAC.ci": ["boa ci", "boa cote d'ivoire", "bank of africa ci", "bank of africa cote d'ivoire"],
    "BOAM.ml": ["boa mali", "bank of africa mali"],
    "BOAN.ne": ["boa niger", "bank of africa niger"],
    "BOAS.sn": ["boa senegal", "bank of africa senegal"],
    "CABC.ci": ["sicable"],
    "CBIBF.bf": ["coris bank international bf", "coris bank bf", "coris bank international burkina", "coris bank burkina"],
    "CFAC.ci": ["cfao motors", "cfao ci"],
    "CIEC.ci": ["cie ci", "compagnie ivoirienne d'electricite"],
    "ECOC.ci": ["ecobank ci", "ecobank cote d'ivoire"],
    "ETIT.tg": ["groupe ecobank", "ecobank transnational"],
    "FTSC.ci": ["filtisac"],
    "LNBB.bj": ["loterie nationale du benin", "lonab"],
    "NEIC.ci": ["nei-ceda", "nei ceda"],
    "NSBC.ci": ["nsia banque"],
    "NTLC.ci": ["nestle ci", "nestle cote d'ivoire"],
    "ONTBF.bf": ["onatel"],
    "ORAC.ci": ["orange ci", "orange cote d'ivoire"],
    "ORGT.tg": ["oragroup"],
    "PALC.ci": ["palmci"],
    "PRSC.ci": ["tractafric"],
    "SAFC.ci": ["safca"],
    "SCRC.ci": ["sucrivoire"],
    "SDCC.ci": ["sodeci"],
    "SDSC.ci": ["agl ci", "africa global logistics", "bollore africa logistics"],
    "SEMC.ci": ["crown siem", "sonoco packaging siem"],
    "SGBC.ci": ["sgbci", "societe generale cote d'ivoire", "societe generale ci"],
    "SHEC.ci": ["vivo energy"],
    "SIBC.ci": ["societe ivoirienne de banque", "sib ci"],
    "SICC.ci": ["sicor"],
    "SIVC.ci": ["erium"],
    "SLBC.ci": ["solibra"],
    "SMBC.ci": ["smb ci"],
    "SNTS.sn": ["sonatel"],
    "SOGC.ci": ["sogb"],
    "SPHC.ci": ["saph ci"],
    "STAC.ci": ["setao"],
    "STBC.ci": ["sitab"],
    "SVOC.ci": ["movis"],
    "TTLC.ci": ["totalenergies marketing ci", "totalenergies marketing cote d'ivoire", "total ci", "totalenergies ci"],
    "TTLS.sn": ["total senegal", "totalenergies senegal"],
    "UNLC.ci": ["unilever ci", "unilever cote d'ivoire"],
    "UNXC.ci": ["uniwax"],
}


def normalize(text):
    """minuscule, sans accents, apostrophes courbes -> droites."""
    if text is None:
        return ""
    text = text.replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()


def connect():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "brvm"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
    )


def build_matcher(patterns_by_key):
    """{cle: [motifs...]} -> [(cle, regex_motif), ...].

    Matching par limite de mot (\\b) : sans ca, le motif "onatel" (ONATEL BF)
    matcherait a tort a l'interieur de "sONATEL" (SNTS.sn).
    """
    pairs = []
    for key, patterns in patterns_by_key.items():
        for p in patterns:
            regex = re.compile(r"\b" + re.escape(normalize(p)) + r"\b")
            pairs.append((key, regex))
    return pairs


def find_matches(titre_norm, matcher):
    return {key for key, regex in matcher if regex.search(titre_norm)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Affiche les stats sans modifier la base")
    parser.add_argument(
        "--export-preview",
        metavar="FICHIER.csv",
        help="Ecrit la liste complete des correspondances (matches et ambigus) dans un CSV, pour revue avant application",
    )
    args = parser.parse_args()

    conn = connect()

    with conn.cursor() as cur:
        cur.execute("SELECT ticker, action_id FROM actions")
        action_id_by_ticker = dict(cur.fetchall())
        cur.execute("SELECT code, indice_id FROM indices")
        indice_id_by_code = dict(cur.fetchall())
        cur.execute("SELECT code, nom FROM indices")
        index_patterns = {code: [nom] for code, nom in cur.fetchall()}
        cur.execute("SELECT source_id, titre FROM news")
        news_rows = cur.fetchall()

    action_matcher = build_matcher(ACTION_PATTERNS)
    index_matcher = build_matcher(index_patterns)

    action_updates = []
    action_matches = []  # (source_id, ticker, titre) - pour l'export de revue
    indice_updates = []
    ambiguous_action = []
    ambiguous_index = []

    for source_id, titre in news_rows:
        titre_norm = normalize(titre)

        action_hits = find_matches(titre_norm, action_matcher)
        if len(action_hits) == 1:
            ticker = next(iter(action_hits))
            action_updates.append((source_id, action_id_by_ticker[ticker]))
            action_matches.append((source_id, ticker, titre))
        elif len(action_hits) > 1:
            ambiguous_action.append((source_id, titre, action_hits))

        index_hits = find_matches(titre_norm, index_matcher)
        if len(index_hits) == 1:
            code = next(iter(index_hits))
            indice_updates.append((source_id, indice_id_by_code[code]))
        elif len(index_hits) > 1:
            ambiguous_index.append((source_id, titre, index_hits))

    print(f"Articles totaux              : {len(news_rows)}")
    print(f"Lies a une action (1 match)   : {len(action_updates)}")
    print(f"Ambigus (>1 action)           : {len(ambiguous_action)}")
    print(f"Lies a un indice (1 match)    : {len(indice_updates)}")
    print(f"Ambigus (>1 indice)           : {len(ambiguous_index)}")
    print(f"Non lies (aucun match)        : {len(news_rows) - len(action_updates) - len(ambiguous_action) - len(indice_updates) - len(ambiguous_index)}  (approx, un article peut compter dans plusieurs categories)")

    if ambiguous_action:
        print("\nExemples d'articles ambigus (plusieurs societes citees, non lies) :")
        for source_id, titre, hits in ambiguous_action[:5]:
            print(f"  [{source_id}] {titre!r} -> {hits}")

    if args.export_preview:
        with open(args.export_preview, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["type", "source_id", "ticker_ou_indices", "titre"])
            for source_id, ticker, titre in action_matches:
                writer.writerow(["match_action", source_id, ticker, titre])
            for source_id, titre, hits in ambiguous_action:
                writer.writerow(["ambigu_action", source_id, ",".join(sorted(hits)), titre])
        print(f"\nExport ecrit dans {args.export_preview} ({len(action_matches)} matches, {len(ambiguous_action)} ambigus)")

    if args.dry_run:
        print("\n--dry-run: aucune ecriture effectuee.")
        conn.close()
        return

    with conn.cursor() as cur:
        if action_updates:
            execute_values(
                cur,
                """
                UPDATE news SET action_id = v.action_id
                FROM (VALUES %s) AS v(source_id, action_id)
                WHERE news.source_id = v.source_id
                """,
                action_updates,
            )
        if indice_updates:
            execute_values(
                cur,
                """
                UPDATE news SET indice_id = v.indice_id
                FROM (VALUES %s) AS v(source_id, indice_id)
                WHERE news.source_id = v.source_id
                """,
                indice_updates,
            )
    conn.commit()
    conn.close()
    print("\nMise a jour appliquee.")


if __name__ == "__main__":
    main()
