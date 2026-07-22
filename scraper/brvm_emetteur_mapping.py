"""
Mapping Emetteur BRVM <-> Ticker Sikafinance + Nom canonique
============================================================
Source de vérité unique pour homogénéiser les scrapers de dividendes
(brvm.org et sikafinance.com) et les autres scrapers du projet.

Convention :
- Ticker  : format Sikafinance ('SNTS.sn', 'BOAS.sn', ...) — aligné sur
            brvm_tickers.ACTIONS et sur les fichiers data/actions/*.csv.
- Nom_Canonique : forme courte cohérente (issue de BRVM, normalisée).

Les émetteurs présents sur brvm.org mais sans ticker BRVM actif
(sociétés retirées, fusionnées, rebrandées) sont mappés vers leur
ticker historique pour préserver la continuité (ex. BOLLORE TRANSPORT
-> SDSC.ci après rebrand en Africa Global Logistics).
"""

from __future__ import annotations

# Clé = libellé exact tel qu'affiché sur brvm.org/fr/esv/paiement-de-dividendes
# Valeur = (ticker_sikafinance, nom_canonique)
EMETTEUR_BRVM_TO_TICKER: dict[str, tuple[str, str]] = {
    "AIR LIQUIDE CI":                 ("SIVC.ci",  "AIR LIQUIDE CI"),
    "BANK OF AFRICA BF":              ("BOABF.bf", "BANK OF AFRICA BURKINA FASO"),
    "BANK OF AFRICA BN":              ("BOAB.bj",  "BANK OF AFRICA BENIN"),
    "BANK OF AFRICA CI":              ("BOAC.ci",  "BANK OF AFRICA COTE D'IVOIRE"),
    "BANK OF AFRICA ML":              ("BOAM.ml",  "BANK OF AFRICA MALI"),
    "BANK OF AFRICA NG":              ("BOAN.ne",  "BANK OF AFRICA NIGER"),
    "BANK OF AFRICA SN":              ("BOAS.sn",  "BANK OF AFRICA SENEGAL"),
    "BERNABE CI":                     ("BNBC.ci",  "BERNABE CI"),
    "BICI CI":                        ("BICC.ci",  "BICICI"),
    "BIIC":                           ("BICB.bj",  "BANQUE INTERNATIONALE INDUSTRIES COMMERCE BENIN"),
    "BOLLORE TRANSPORT & LOGISTICS":  ("SDSC.ci",  "AFRICA GLOBAL LOGISTICS CI"),
    "CFAO MOTORS CI":                 ("CFAC.ci",  "CFAO MOTORS CI"),
    "CIE CI":                         ("CIEC.ci",  "CIE CI"),
    "CORIS BANK INTERNATIONAL":       ("CBIBF.bf", "CORIS BANK INTERNATIONAL BF"),
    "CROWN SIEM CI":                  ("SEMC.ci",  "CROWN SIEM CI"),
    "ECOBANK CI":                     ("ECOC.ci",  "ECOBANK CI"),
    "ECOBANK TG":                     ("ETIT.tg",  "ECOBANK TRANSNATIONAL INC TG"),
    "FILTISAC CI":                    ("FTSC.ci",  "FILTISAC CI"),
    "LNB":                            ("LNBB.bj",  "LOTERIE NATIONALE DU BENIN"),
    "NEI-CEDA CI":                    ("NEIC.ci",  "NEI-CEDA CI"),
    "NESTLE CI":                      ("NTLC.ci",  "NESTLE CI"),
    "NSBC":                           ("NSBC.ci",  "NSIA BANQUE CI"),
    "ONATEL BF":                      ("ONTBF.bf", "ONATEL BF"),
    "ORAGROUP":                       ("ORGT.tg",  "ORAGROUP TG"),
    "ORANGE CI":                      ("ORAC.ci",  "ORANGE CI"),
    "PALM CI":                        ("PALC.ci",  "PALMCI"),
    "SAPH CI":                        ("SPHC.ci",  "SAPH CI"),
    "SERVAIR ABIDJAN CI":             ("ABJC.ci",  "SERVAIR ABIDJAN CI"),
    "SETAO CI":                       ("STAC.ci",  "SETAO CI"),
    "SGCI":                           ("SGBC.ci",  "SGBCI"),
    "SIB":                            ("SIBC.ci",  "SOCIETE IVOIRIENNE DE BANQUE CI"),
    "SICABLE":                        ("CABC.ci",  "SICABLE CI"),
    "SITAB":                          ("STBC.ci",  "SITAB CI"),
    "SMB":                            ("SMBC.ci",  "SMB CI"),
    "SODECI":                         ("SDCC.ci",  "SODECI"),
    "SOGB":                           ("SOGC.ci",  "SOGB"),
    "SOLIBRA":                        ("SLBC.ci",  "SOLIBRA CI"),
    "SONATEL":                        ("SNTS.sn",  "SONATEL"),
    "SUCRIVOIRE":                     ("SCRC.ci",  "SUCRIVOIRE"),
    "TOTAL":                          ("TTLC.ci",  "VIVO ENERGY CI"),         # ex-TOTAL CI
    "TOTAL SENEGAL S.A.":             ("TTLS.sn",  "VIVO ENERGY SENEGAL"),    # ex-TOTAL SN
    "TRACTAFRIC CI":                  ("PRSC.ci",  "TRACTAFRIC MOTORS CI"),
    "UNIWAX CI":                      ("UNXC.ci",  "UNIWAX CI"),
    "VIVO ENERGY CI":                 ("SHEC.ci",  "VIVO ENERGY CI"),         # ex-SHELL CI
}

# Index inverse Ticker -> Nom_Canonique (pour les scripts qui partent du ticker)
TICKER_TO_NOM_CANONIQUE: dict[str, str] = {
    ticker: nom for (ticker, nom) in EMETTEUR_BRVM_TO_TICKER.values()
}


def lookup_brvm(emetteur: str) -> tuple[str, str]:
    """Retourne (ticker, nom_canonique) pour un émetteur BRVM.

    Renvoie ('', emetteur) si l'émetteur est inconnu — l'appelant doit
    logger ce cas afin que le mapping soit complété ici.
    """
    if not emetteur:
        return "", ""
    return EMETTEUR_BRVM_TO_TICKER.get(emetteur.strip(), ("", emetteur.strip()))


def lookup_ticker(ticker: str) -> str:
    """Retourne le nom canonique associé à un ticker, ou '' si inconnu."""
    return TICKER_TO_NOM_CANONIQUE.get(ticker, "")
