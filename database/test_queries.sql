-- =====================================================================
-- Requetes de test / exploration - base brvm
-- =====================================================================

-- 1. Les 10 plus grosses capitalisations boursieres, par secteur
SELECT ticker, nom, secteur, valorisation_mfcfa, flottant_pct
FROM actions
ORDER BY valorisation_mfcfa DESC NULLS LAST
LIMIT 10;

-- 2. Dernier cours connu de chaque action (DISTINCT ON = dernier par ticker)
SELECT DISTINCT ON (a.ticker)
    a.ticker, a.nom, h.date, h.cloture, h.variation_pct
FROM historique_actions h
JOIN actions a ON a.action_id = h.action_id
ORDER BY a.ticker, h.date DESC;

-- 3. Evolution chiffre d'affaires / resultat net d'une societe precise (exemple SGBC.ci)
SELECT a.ticker, f.exercice, f.chiffre_affaires_mfcfa, f.croissance_ca_pct,
       f.resultat_net_mfcfa, f.croissance_rn_pct, f.bnpa_fcfa, f.per
FROM fondamentaux f
JOIN actions a ON a.action_id = f.action_id
WHERE a.ticker = 'SGBC.ci'
ORDER BY f.exercice;

-- 4. Top 10 des meilleurs rendements de dividendes (exercice 2025)
SELECT a.ticker, a.nom, d.exercice, d.statut, d.montant_net_fcfa, d.rendement_pct
FROM dividendes d
JOIN actions a ON a.action_id = d.action_id
WHERE d.exercice = 2025
ORDER BY d.rendement_pct DESC NULLS LAST
LIMIT 10;

-- 5. Capitalisation totale et nombre de societes par secteur
SELECT secteur, count(*) AS nb_societes, sum(valorisation_mfcfa) AS capi_totale_mfcfa
FROM actions
GROUP BY secteur
ORDER BY capi_totale_mfcfa DESC NULLS LAST;

-- 6. Performance BRVM30 sur les 12 derniers mois (premier vs dernier cours de cloture)
SELECT
    i.code,
    min(h.date) AS premiere_date,
    max(h.date) AS derniere_date,
    (array_agg(h.cloture ORDER BY h.date ASC))[1]  AS cloture_debut,
    (array_agg(h.cloture ORDER BY h.date DESC))[1] AS cloture_fin
FROM historique_indices h
JOIN indices i ON i.indice_id = h.indice_id
WHERE i.code = 'BRVM30'
  AND h.date >= (SELECT max(date) FROM historique_indices) - INTERVAL '365 days'
GROUP BY i.code;

-- 7. Nombre d'articles de news par mois (tendance de publication)
SELECT date_trunc('month', date_publication)::date AS mois, count(*) AS nb_articles
FROM news
GROUP BY mois
ORDER BY mois DESC
LIMIT 12;

-- 8. Dividendes "A venir" avec la derniere cloture connue de l'action (pour calculer le rendement soi-meme)
SELECT a.ticker, a.nom, d.date_detachement, d.date_paiement, d.montant_net_fcfa,
       h.cloture AS dernier_cours, h.date AS date_dernier_cours
FROM dividendes d
JOIN actions a ON a.action_id = d.action_id
LEFT JOIN LATERAL (
    SELECT cloture, date
    FROM historique_actions
    WHERE action_id = a.action_id
    ORDER BY date DESC
    LIMIT 1
) h ON true
WHERE d.statut = 'A venir'
ORDER BY d.date_detachement;

-- 9. Verification d'integrite : actions sans aucun historique de cours (nouvelles introductions)
SELECT a.ticker, a.nom, a.date_maj
FROM actions a
LEFT JOIN historique_actions h ON h.action_id = a.action_id
WHERE h.action_id IS NULL;

-- 10. Nombre de lignes par table (sanity check rapide)
SELECT 'actions' AS table_name, count(*) FROM actions
UNION ALL SELECT 'indices', count(*) FROM indices
UNION ALL SELECT 'historique_actions', count(*) FROM historique_actions
UNION ALL SELECT 'historique_indices', count(*) FROM historique_indices
UNION ALL SELECT 'dividendes', count(*) FROM dividendes
UNION ALL SELECT 'fondamentaux', count(*) FROM fondamentaux
UNION ALL SELECT 'news', count(*) FROM news;
