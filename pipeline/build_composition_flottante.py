"""Construit data/Composition_flottante.xlsx.

Pour chaque actif et chaque date :
    valeur_FCFA = cours (Cloture) x nombre de titres

La pondération quotidienne d'un actif = valeur_FCFA / total du marché ce jour-là.

Avant d'écrire le fichier, on vérifie que le total quotidien calculé
correspond bien à l'indice de capitalisation CAPIBRVM_historique.csv.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ACTIONS_DIR = DATA_DIR / "actions"
SOCIETES_DIR = DATA_DIR / "societes"
CAPI_CSV = DATA_DIR / "indices" / "CAPIBRVM_historique.csv"
OUTPUT_XLSX = DATA_DIR / "Composition_flottante.xlsx"

SHARE_KEYS = ("Nombre_Titres", "Nombre_Actions", "Nombre de titres")


def parse_int(value: str | None) -> int | None:
    """Convertit '40 000 000' -> 40000000."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def load_shares() -> dict[str, int]:
    """Mappe le code actif (ex: 'BOAC') vers son nombre de titres."""
    shares: dict[str, int] = {}
    for json_path in SOCIETES_DIR.glob("*_societe.json"):
        code = json_path.name.split("_", 1)[0].upper()
        try:
            info = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  [WARN] JSON illisible : {json_path.name}")
            continue
        n = next(
            (parse_int(info[k]) for k in SHARE_KEYS if info.get(k)),
            None,
        )
        if n:
            shares[code] = n
        else:
            print(f"  [WARN] Nombre de titres manquant : {json_path.name}")
    return shares


def load_prices() -> dict[str, pd.Series]:
    """Mappe le code actif vers une série Cloture indexée par date."""
    prices: dict[str, pd.Series] = {}
    for csv_path in ACTIONS_DIR.glob("*_historique.csv"):
        code = csv_path.name.split("_", 1)[0].upper()
        df = pd.read_csv(csv_path, sep=";", decimal=",")
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Cloture"])
        serie = df.set_index("Date")["Cloture"].sort_index()
        serie = serie[~serie.index.duplicated(keep="last")]
        prices[code] = serie
    return prices


def load_capibrvm() -> pd.Series:
    df = pd.read_csv(CAPI_CSV, sep=";", decimal=",")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Cloture"])
    return df.set_index("Date")["Cloture"].sort_index()


def main() -> None:
    print("1) Chargement des nombres de titres...")
    shares = load_shares()
    print(f"   {len(shares)} actifs avec nombre de titres.")

    print("2) Chargement des cours historiques...")
    prices = load_prices()
    print(f"   {len(prices)} actifs avec historique de cours.")

    codes = sorted(set(shares) & set(prices))
    missing = sorted(set(prices) - set(shares))
    if missing:
        print(f"   [WARN] Sans nombre de titres (ignorés) : {', '.join(missing)}")
    print(f"   {len(codes)} actifs retenus.")

    # Matrice des valeurs FCFA = cours x titres
    valeurs = pd.DataFrame(
        {code: prices[code] * shares[code] for code in codes}
    ).sort_index()

    # On ne garde que les jours où au moins un cours est dispo ;
    # forward-fill pour propager le dernier cours connu de chaque actif.
    valeurs = valeurs.ffill()

    total = valeurs.sum(axis=1, skipna=True)

    # 3) Vérification vs CAPIBRVM
    print("3) Vérification du total vs CAPIBRVM_historique.csv...")
    capi = load_capibrvm()
    # CAPIBRVM est exprimé en MILLIONS de FCFA -> on remet en FCFA.
    capi_fcfa = capi * 1_000_000.0
    verif = pd.DataFrame(
        {"total_calcule_FCFA": total, "capibrvm_FCFA": capi_fcfa}
    ).dropna()
    verif["ratio_calc/capi"] = (
        verif["total_calcule_FCFA"] / verif["capibrvm_FCFA"]
    )
    verif["ecart_pct"] = (verif["ratio_calc/capi"] - 1.0) * 100.0
    if not verif.empty:
        print(
            f"   Période comparée : {verif.index.min().date()} -> "
            f"{verif.index.max().date()} ({len(verif)} jours)"
        )
        print(f"   Ratio moyen   : {verif['ratio_calc/capi'].mean():.4f}")
        print(f"   Ratio médian  : {verif['ratio_calc/capi'].median():.4f}")
        print(
            f"   Écart absolu moyen : {verif['ecart_pct'].abs().mean():.2f} %"
        )
        last = verif.iloc[-1]
        print(
            f"   Dernier jour ({verif.index[-1].date()}) : "
            f"calculé={last['total_calcule_FCFA']:,.0f} FCFA | "
            f"CAPIBRVM={last['capibrvm_FCFA']:,.0f} FCFA | "
            f"écart={last['ecart_pct']:+.2f} %"
        )
    else:
        print("   [WARN] Aucune date commune avec CAPIBRVM.")

    # 4) Pondérations quotidiennes
    print("4) Calcul des pondérations quotidiennes...")
    poids = valeurs.div(total, axis=0)
    poids = poids.dropna(how="all")
    poids.index.name = "Date"

    # 5) Écriture du fichier Excel
    print(f"5) Écriture de {OUTPUT_XLSX.relative_to(BASE_DIR)} ...")
    poids_out = poids.reset_index()
    poids_out["Date"] = poids_out["Date"].dt.strftime("%Y-%m-%d")

    verif_out = verif.reset_index().rename(columns={"index": "Date"})
    if not verif_out.empty:
        verif_out["Date"] = verif_out["Date"].dt.strftime("%Y-%m-%d")

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        poids_out.to_excel(writer, sheet_name="Ponderations_totales", index=False)
        verif_out.to_excel(writer, sheet_name="Verification", index=False)

    print(
        f"   OK : {poids.shape[0]} dates x {poids.shape[1]} actifs."
    )


if __name__ == "__main__":
    main()
