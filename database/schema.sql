-- =====================================================================
-- BRVM Market Data Warehouse — PostgreSQL schema
-- =====================================================================
-- Source datasets inspected (data/ folder of this repo):
--   data/societes/*_societe.json        -> feeds `actions` (company profile)
--   data/actions/*_historique.csv       -> feeds `historique_actions` (OHLCV)
--   data/dividendes/dividendes.csv      -> feeds `dividendes`
--   data/fondamentaux/*_fondamentaux.json -> feeds `fondamentaux`
--   data/indices/*_info.json            -> feeds `indices`
--   data/indices/*_historique.csv       -> feeds `historique_indices`
--   data/news/actualites_brvm.csv       -> feeds `news`
--
-- Design notes:
--   * All surrogate keys use GENERATED ALWAYS AS IDENTITY (modern Postgres
--     equivalent of SERIAL, avoids manual sequence grants).
--   * Monetary amounts are stored in FCFA unless a column name says
--     otherwise (e.g. *_mfcfa = millions of FCFA, matching the source
--     site's own unit for "Valorisation").
--   * NUMERIC (not FLOAT/REAL) is used everywhere money or ratios are
--     involved, to avoid floating-point rounding drift.
--   * Every fact table that represents "one company/index + one date" or
--     "one company + one reporting period" carries a UNIQUE constraint on
--     that pair, per the no-duplicates requirement.
-- =====================================================================


-- =====================================================================
-- 1. ACTIONS — reference table for BRVM-listed companies
-- =====================================================================
-- One row per listed company. This is the parent table that every
-- company-specific table (dividendes, fondamentaux, historique_actions,
-- news) links back to via action_id.
--
-- Source: data/societes/*_societe.json (48 files, one per company).
-- Fields "Chiffre d'affaires", "Résultat net", "BNPA", "PER", "Dividende"
-- present in that JSON are DELIBERATELY NOT copied here: they are just a
-- snapshot of the latest year already covered by `fondamentaux`. Storing
-- them again here would duplicate data and risk going stale.
-- The "Conseil_*" fields (daily analyst commentary) are also excluded —
-- see the open questions section for why.
CREATE TABLE actions (
    action_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Identifiers
    ticker           VARCHAR(15) NOT NULL UNIQUE,   -- e.g. 'ABJC.ci' (source canonical format: TICKER.pays)
    isin             VARCHAR(12) UNIQUE,             -- e.g. 'CI0000000600'
    pays_code        VARCHAR(2)  NOT NULL,           -- UEMOA member code parsed from the ticker suffix

    -- Company profile (raw, from source)
    nom              VARCHAR(200) NOT NULL,
    secteur          VARCHAR(100),
    adresse          TEXT,
    telephone        VARCHAR(100),
    fax              VARCHAR(100),
    dirigeants       TEXT,
    description      TEXT,

    -- Capital structure (raw, from source; refreshed on each scrape)
    nombre_actions   BIGINT       CHECK (nombre_actions >= 0),
    flottant_pct     NUMERIC(5,2) CHECK (flottant_pct BETWEEN 0 AND 100),
    valorisation_mfcfa NUMERIC(18,2) CHECK (valorisation_mfcfa >= 0), -- in millions FCFA, matches source unit

    -- Bookkeeping
    date_maj         DATE,                            -- date this profile snapshot was last scraped
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_actions_pays_code
        CHECK (pays_code IN ('ci','bj','bf','ml','ne','sn','tg','gw'))
);

COMMENT ON TABLE actions IS
    'Reference table: one row per BRVM-listed company. All company-specific facts (dividends, fundamentals, price history, news) hang off action_id.';
COMMENT ON COLUMN actions.ticker IS
    'Canonical ticker as used across all source files, format TICKER.country (e.g. ABJC.ci). Unique business key.';
COMMENT ON COLUMN actions.pays_code IS
    'UEMOA country code parsed from the ticker suffix: ci=Cote d''Ivoire, bj=Benin, bf=Burkina Faso, ml=Mali, ne=Niger, sn=Senegal, tg=Togo, gw=Guinee-Bissau.';
COMMENT ON COLUMN actions.valorisation_mfcfa IS
    'Market capitalisation in millions of FCFA, as published by the source (field was "Valorisation" / "MFCFA" suffix in raw JSON).';

CREATE INDEX idx_actions_secteur ON actions (secteur);
CREATE INDEX idx_actions_pays_code ON actions (pays_code);


-- =====================================================================
-- 2. INDICES — reference table for BRVM market indices
-- =====================================================================
-- Source: data/indices/*_info.json (13 files). Fields available there are
-- minimal (Symbol, Name, ISIN — ISIN missing for 3 of them). `categorie`
-- and `fournisseur` below are NOT present in the raw source; they are an
-- inferred classification (see explanation in the chat response / open
-- questions). Treat them as an editorial add-on you can correct freely.
CREATE TABLE indices (
    indice_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    code             VARCHAR(20) NOT NULL UNIQUE,   -- e.g. 'BRVM30', 'BRVM-TEL' (source "Symbol")
    nom              VARCHAR(150) NOT NULL,          -- source "Name"
    isin             VARCHAR(12) UNIQUE,             -- NULL for CAPIBRVM, SIKAIDX, SIKATR (no ISIN published)
    categorie        VARCHAR(30),                    -- inferred: 'Phare' | 'Composite' | 'Compartiment' | 'Sectoriel' | 'Capitalisation' | 'Tiers'
    fournisseur      VARCHAR(30) NOT NULL DEFAULT 'BRVM', -- 'BRVM' (official) vs 'SikaFinance' (third-party index)
    description      TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_indices_categorie
        CHECK (categorie IS NULL OR categorie IN
            ('Phare','Composite','Compartiment','Sectoriel','Capitalisation','Tiers'))
);

COMMENT ON TABLE indices IS
    'Reference table: one row per BRVM (or BRVM-related third-party) index.';
COMMENT ON COLUMN indices.categorie IS
    'Editorial classification inferred from index names, NOT present in source data — confirm with supervisor before relying on it for reporting.';
COMMENT ON COLUMN indices.fournisseur IS
    'CAPIBRVM/SIKAIDX/SIKATR have no ISIN in source data and appear to be SikaFinance-branded indices rather than official BRVM indices — confirm before treating them as equivalent to BRVM30/BRVMC etc.';


-- =====================================================================
-- 3. HISTORIQUE_ACTIONS — daily OHLCV price history per company
-- =====================================================================
-- Source: data/actions/{TICKER}_historique.csv (47 files, ~3-3600 rows
-- each depending on listing date). Mirrors historique_indices below.
-- No duplicate (ticker, date) pairs were found in the source files.
CREATE TABLE historique_actions (
    historique_action_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_id           INT  NOT NULL REFERENCES actions (action_id) ON DELETE CASCADE,

    date                DATE NOT NULL,
    ouverture           NUMERIC(14,2) CHECK (ouverture >= 0),
    plus_haut           NUMERIC(14,2) CHECK (plus_haut >= 0),
    plus_bas            NUMERIC(14,2) CHECK (plus_bas  >= 0),
    cloture             NUMERIC(14,2) CHECK (cloture   >= 0),
    volume_titres       BIGINT        CHECK (volume_titres >= 0),
    volume_fcfa         NUMERIC(18,2) CHECK (volume_fcfa   >= 0),
    variation_pct       NUMERIC(7,2),  -- calculated by source vs previous close; NULL on first trading day

    CONSTRAINT uq_historique_actions_action_date UNIQUE (action_id, date),
    CONSTRAINT chk_historique_actions_high_low CHECK (plus_haut >= plus_bas)
);

COMMENT ON TABLE historique_actions IS
    'Daily OHLCV price history per company. One row = one company + one trading date.';
COMMENT ON COLUMN historique_actions.variation_pct IS
    'Calculated field (day-over-day % change), taken as-is from source; NULL for the first recorded session of a stock.';

CREATE INDEX idx_historique_actions_action_id ON historique_actions (action_id);
CREATE INDEX idx_historique_actions_date ON historique_actions (date);


-- =====================================================================
-- 4. HISTORIQUE_INDICES — daily OHLCV history per index
-- =====================================================================
-- Source: data/indices/{CODE}_historique.csv. Same shape as
-- historique_actions, linked to `indices` via indice_id instead.
CREATE TABLE historique_indices (
    historique_indice_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    indice_id           INT  NOT NULL REFERENCES indices (indice_id) ON DELETE CASCADE,

    date                DATE NOT NULL,
    ouverture           NUMERIC(14,2) CHECK (ouverture >= 0),
    plus_haut           NUMERIC(14,2) CHECK (plus_haut >= 0),
    plus_bas            NUMERIC(14,2) CHECK (plus_bas  >= 0),
    cloture             NUMERIC(14,2) CHECK (cloture   >= 0),
    volume_titres       BIGINT        CHECK (volume_titres >= 0),
    volume_fcfa         NUMERIC(18,2) CHECK (volume_fcfa   >= 0),
    variation_pct       NUMERIC(7,2),

    CONSTRAINT uq_historique_indices_indice_date UNIQUE (indice_id, date),
    CONSTRAINT chk_historique_indices_high_low CHECK (plus_haut >= plus_bas)
);

COMMENT ON TABLE historique_indices IS
    'Daily OHLCV history per index. One row = one index + one trading date.';

CREATE INDEX idx_historique_indices_indice_id ON historique_indices (indice_id);
CREATE INDEX idx_historique_indices_date ON historique_indices (date);


-- =====================================================================
-- 5. DIVIDENDES — one row per company + fiscal year (exercice)
-- =====================================================================
-- Source: data/dividendes/dividendes.csv (327 rows incl. header = 326
-- records). This is the MASTER file: dividendes_a_venir.csv (5 rows) and
-- dividendes_passes.csv (220 rows) are just filtered exports of the same
-- data (verified: every row in both subsets exists in dividendes.csv) —
-- do NOT import all three, only dividendes.csv (or dividendes.json,
-- identical content).
-- Verified: 0 duplicate (Ticker, Exercice) pairs in the source today.
CREATE TABLE dividendes (
    dividende_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_id           INT NOT NULL REFERENCES actions (action_id) ON DELETE CASCADE,

    exercice            SMALLINT NOT NULL CHECK (exercice BETWEEN 1990 AND 2100), -- fiscal year the dividend relates to
    statut              VARCHAR(15) NOT NULL CHECK (statut IN ('A venir','Passé','A préciser')),
    date_detachement    DATE,           -- ex-dividend date; NULL when statut = 'A préciser'
    date_paiement       DATE,           -- payment date; NULL when statut = 'A préciser'
    montant_net_fcfa    NUMERIC(14,4) CHECK (montant_net_fcfa >= 0), -- net dividend per share, FCFA
    rendement_pct       NUMERIC(6,2),   -- calculated: dividend yield %, computed by source from price at scrape time

    avis_url            TEXT,           -- link to the official BRVM notice (PDF)
    avis_path           TEXT,           -- local archived copy of that notice, if downloaded
    sources             VARCHAR(200),   -- comma-separated list of scraped sources (e.g. 'brvm.org,sikafinance.com')
    date_scraping       DATE NOT NULL,

    CONSTRAINT uq_dividendes_action_exercice UNIQUE (action_id, exercice)
);

COMMENT ON TABLE dividendes IS
    'One row per company per fiscal year (exercice): dividend amount, ex-date, payment date and status.';
COMMENT ON COLUMN dividendes.rendement_pct IS
    'Calculated field: dividend yield at time of scraping (montant_net_fcfa / share price on date_scraping), not a stored raw fact — will drift from any yield you recompute later against a different price date.';
COMMENT ON COLUMN dividendes.statut IS
    'A venir = upcoming/announced, Passé = already paid, A préciser = year known but ex-date/payment/amount not yet published.';

CREATE INDEX idx_dividendes_action_id ON dividendes (action_id);
CREATE INDEX idx_dividendes_exercice ON dividendes (exercice);
CREATE INDEX idx_dividendes_date_paiement ON dividendes (date_paiement);


-- =====================================================================
-- 6. FONDAMENTAUX — one row per company + fiscal year (exercice)
-- =====================================================================
-- Source: data/fondamentaux/*_fondamentaux.json (48 files). Verified: the
-- exact same 7 metric names (Chiffre d'affaires, Croissance CA, Résultat
-- net, Croissance RN, BNPA, PER, Dividende) appear across ALL 48 files
-- with no variation -> structured columns are appropriate, no EAV needed.
-- Not every year has every metric filled (e.g. "Croissance CA" has no
-- value for the first year in the series, since there's no prior year to
-- compare to) — those become NULL, not missing rows.
CREATE TABLE fondamentaux (
    fondamental_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_id           INT NOT NULL REFERENCES actions (action_id) ON DELETE CASCADE,

    exercice            SMALLINT NOT NULL CHECK (exercice BETWEEN 1990 AND 2100),

    chiffre_affaires_mfcfa NUMERIC(16,2), -- raw: revenue, millions FCFA
    croissance_ca_pct      NUMERIC(7,2),  -- calculated: YoY revenue growth %, NULL for the first year available
    resultat_net_mfcfa     NUMERIC(16,2), -- raw: net income, millions FCFA
    croissance_rn_pct      NUMERIC(7,2),  -- calculated: YoY net income growth %, NULL for the first year available
    bnpa_fcfa              NUMERIC(12,2), -- raw: earnings per share, FCFA
    per                    NUMERIC(10,2), -- raw (as published): price/earnings ratio
    dividende_fcfa         NUMERIC(12,2), -- raw: dividend per share, FCFA (also feeds `dividendes` independently)

    source_url          TEXT,
    date_scraping       DATE,           -- NULL: unlike dividendes/news, the source JSON carries no per-file scrape date

    CONSTRAINT uq_fondamentaux_action_exercice UNIQUE (action_id, exercice)
);

COMMENT ON TABLE fondamentaux IS
    'One row per company per fiscal year: revenue, net income, EPS, P/E and DPS, plus their YoY growth rates. Structured columns chosen because all 48 source files expose the exact same 7 indicators.';
COMMENT ON COLUMN fondamentaux.croissance_ca_pct IS
    'Calculated by the source as year-over-year % change of chiffre_affaires_mfcfa; kept as published rather than recomputed so it matches the provider''s own numbers.';
COMMENT ON COLUMN fondamentaux.per IS
    'Price/Earnings ratio as published by the source; depends on the share price on the day it was computed, so it is not purely a "fundamental" (flag if you need a point-in-time PER recomputed against historique_actions.cloture instead).';

CREATE INDEX idx_fondamentaux_action_id ON fondamentaux (action_id);
CREATE INDEX idx_fondamentaux_exercice ON fondamentaux (exercice);


-- =====================================================================
-- 7. NEWS — general BRVM / market news, optionally linked to a company
--    and/or an index
-- =====================================================================
-- Source: data/news/actualites_brvm.csv (5613 rows). The "id" column in
-- the source is the source site's own article id (verified unique) and
-- is kept as a natural key (source_id) alongside a surrogate news_id.
-- IMPORTANT: the raw source has NO column linking an article to a
-- specific ticker or index — action_id / indice_id below start out NULL
-- for every imported row. Populating them requires your own matching
-- logic (e.g. ticker/company-name matching against titre/contenu) run
-- during or after import; see open questions.
CREATE TABLE news (
    news_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id           BIGINT NOT NULL UNIQUE,     -- source site's own article id

    action_id           INT REFERENCES actions (action_id) ON DELETE SET NULL, -- NULL = not linked to a specific company
    indice_id           INT REFERENCES indices (indice_id) ON DELETE SET NULL, -- NULL = not linked to a specific index

    titre               TEXT NOT NULL,
    date_publication    TIMESTAMPTZ NOT NULL,
    auteur              VARCHAR(150),
    categorie           VARCHAR(100),   -- always empty in the current source export; column kept for when it gets populated
    contenu             TEXT,
    image_url           TEXT,
    url                 TEXT NOT NULL UNIQUE,
    date_scraping       DATE NOT NULL
);

COMMENT ON TABLE news IS
    'BRVM / regional market news items. A row may stand alone as general news (action_id and indice_id both NULL) or be tagged to a specific company and/or index once you implement that matching.';
COMMENT ON COLUMN news.source_id IS
    'Article id as assigned by the scraped source site — verified unique across the current export, used to avoid re-inserting the same article on repeated scrapes.';

CREATE INDEX idx_news_date_publication ON news (date_publication);
CREATE INDEX idx_news_action_id ON news (action_id);
CREATE INDEX idx_news_indice_id ON news (indice_id);
