import json, re
from pathlib import Path
import pandas as pd

BASE = Path("data")

def parse_num(v):
    if not v:
        return None
    return float(re.sub(r"[^\d,]", "", str(v)).replace(",", ".") or "0") or None

# ── Question 2 : Nombre_Actions = TOTAL ou FLOTTANT ? ─────────────────────
# Méthode : Valorisation (MFCFA) / Nombre_Actions  → cours implicite
# On compare ce cours implicite au dernier Cloture du CSV.
# Si ratio ≈ 1  → Nombre_Actions est le TOTAL (comme attendu)
# Si ratio ≈ Flottant_Pct → Nombre_Actions serait le flottant

print("=== Nombre_Actions = TOTAL ou FLOTTANT ? ===")
header = f"{'Ticker':<8} {'NbActions':>12} {'Float%':>7} {'Valo MFCFA':>11} {'Cours_impl':>11} {'Dernier_cours':>14} {'Ratio':>7}"
print(header)
print("-" * len(header))

for jf in sorted((BASE / "societes").glob("*_societe.json"))[:12]:
    d = json.loads(jf.read_text(encoding="utf-8"))
    code = jf.name.split("_")[0].upper()
    nb = parse_num(d.get("Nombre_Titres") or d.get("Nombre_Actions"))
    valo_raw = d.get("Valorisation") or d.get("Valorisation de la société", "")
    # ex: "354 800 MFCFA"  → garder les chiffres avant le premier espace-texte
    valo_num = re.sub(r"[^\d\s]", "", valo_raw).split()
    valo = float("".join(valo_num[:2])) if len(valo_num) >= 2 else (float(valo_num[0]) if valo_num else None)
    fp_str = d.get("Flottant_Pct", "0").replace("%", "").replace(",", ".")
    fp = float(fp_str) if fp_str else 0.0

    if not nb or not valo:
        continue

    cours_impl = (valo * 1e6) / nb

    csvs = list((BASE / "actions").glob(f"{code}_*_historique.csv"))
    dernier = None
    if csvs:
        df = pd.read_csv(csvs[0], sep=";", decimal=",")
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Cloture"])
        if not df.empty:
            dernier = df.sort_values("Date").iloc[-1]["Cloture"]

    ratio = round(cours_impl / dernier, 3) if dernier else None
    dc = str(round(dernier, 1)) if dernier else "N/A"
    r  = str(ratio) if ratio else "N/A"
    print(f"{code:<8} {int(nb):>12,} {fp:>6.1f}% {valo:>11,.0f} {cours_impl:>11,.1f} {dc:>14} {r:>7}")

print()
print("Interpretation : si Ratio ≈ 1.0, Nombre_Actions = TOTAL des titres")
print("                 si Ratio ≈ Flottant%, Nombre_Actions = titres du flottant seulement")

# ── Question 1 : CAPIBRVM = capitalisation TOTALE ou FLOTTANTE ? ──────────
print()
print("=== CAPIBRVM = capitalisation TOTALE ou FLOTTANTE ? ===")

capi = pd.read_csv(BASE / "indices" / "CAPIBRVM_historique.csv", sep=";", decimal=",")
capi["Date"] = pd.to_datetime(capi["Date"])
capi_series = capi.set_index("Date")["Cloture"].sort_index() * 1_000_000  # en FCFA

shares, floats = {}, {}
for jf in (BASE / "societes").glob("*_societe.json"):
    d = json.loads(jf.read_text(encoding="utf-8"))
    code = jf.name.split("_")[0].upper()
    nb = parse_num(d.get("Nombre_Titres") or d.get("Nombre_Actions"))
    fp_str = d.get("Flottant_Pct", "0").replace("%", "").replace(",", ".")
    fp = float(fp_str) / 100 if fp_str else 0.0
    if nb:
        shares[code] = nb
        floats[code] = fp

prices = {}
for csv_f in (BASE / "actions").glob("*_historique.csv"):
    code = csv_f.name.split("_")[0].upper()
    df = pd.read_csv(csv_f, sep=";", decimal=",")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    s = df.dropna(subset=["Date", "Cloture"]).set_index("Date")["Cloture"]
    prices[code] = s[~s.index.duplicated(keep="last")]

codes = sorted(set(shares) & set(prices))

# 30 dernières dates de CAPIBRVM
last_dates = capi_series.index[-30:]

rows = []
for dt in last_dates:
    tot = sum(
        prices[c].asof(dt) * shares[c]
        for c in codes if not pd.isna(prices[c].asof(dt))
    )
    flot = sum(
        prices[c].asof(dt) * shares[c] * floats.get(c, 1.0)
        for c in codes if not pd.isna(prices[c].asof(dt))
    )
    capibrvm = capi_series.asof(dt)
    rows.append({
        "ratio_total":    tot  / capibrvm,
        "ratio_flottant": flot / capibrvm,
    })

r = pd.DataFrame(rows)
rt  = r["ratio_total"].mean()
rf  = r["ratio_flottant"].mean()
print(f"Ratio moyen  (cours × nb_total)           / CAPIBRVM : {rt:.4f}")
print(f"Ratio moyen  (cours × nb_total × float%)  / CAPIBRVM : {rf:.4f}")
print()
if abs(rt - 1) < abs(rf - 1):
    print("=> CAPIBRVM = CAPITALISATION TOTALE  (cours × nombre total de titres)")
else:
    print("=> CAPIBRVM = CAPITALISATION FLOTTANTE (cours × nombre total × float%)")
