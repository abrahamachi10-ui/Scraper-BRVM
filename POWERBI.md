# Dashboard Power BI — BRVM

Rapport Power BI (`dash brvm Power BI.pbip`) connecté **directement au dépôt
GitHub** via l'API authentifiée. Les workflows existants mettent à jour les
données dans `data/`, et l'actualisation Power BI rejoue les mêmes requêtes —
aucune copie locale ni passerelle n'est nécessaire pour le service Power BI.

## Pages

| Page | Contenu |
|---|---|
| **Vue d'ensemble** | KPIs marché (nb sociétés, BRVM Composite, volume, signaux ACHAT, potentiel moyen), évolution du BRVM Composite, volume par société, répartition des signaux, table des signaux par valeur, rendement des dividendes. Filtres : Année, Pays, Statut dividende. |
| **Détail action** | Filtre Société. Cartes (dernier cours, performance, PER, volume), carte signal quantitatif (signal / confiance / tendance / cible 30j / potentiel), cours de clôture, volume, matrice des fondamentaux annuels, historique des dividendes. |
| **Détail indice** | Filtre Indice. Cartes (niveau, performance, plus haut/bas), évolution de l'indice, volume, table de comparaison de tous les indices. |

## Modèle de données

Tables (toutes en import, source = API GitHub) :

- `Cours_Actions`, `Cours_Indices` — historiques OHLCV (un fichier CSV par valeur, agrégés).
- `Societes`, `Indices_Info` — dimensions (nom, pays, PER, flottant… / nom, ISIN).
- `Signaux` — dernier signal Prophet + Black-Litterman par action (+ potentiel calculé).
- `Dividendes` — dividendes à venir / passés.
- `Fondamentaux` — CA, RN, BNPA, PER, dividende sur 5 ans (format long : Ticker / Metric / Année / Valeur).
- `Calendrier` — table de dates (relations actives sur les cours).

Relations en étoile autour de `Societes` (Ticker) et `Calendrier` (Date),
`Indices_Info` (Symbol) pour les indices.

## Paramètres (Power Query)

| Paramètre | Valeur | Rôle |
|---|---|---|
| `Owner` | `DylaneTrader` | Propriétaire du dépôt |
| `Repo` | `Scraper-BRVM` | Nom du dépôt |
| `Branche` | `main` | Branche lue |
| `TokenGitHub` | *(vide)* | **PAT GitHub** lecture seule — voir ci-dessous |

> ⚠️ `TokenGitHub` est **laissé vide dans le dépôt**. Ne committez jamais une
> valeur de token. Renseignez-le localement (Power BI Desktop) pour tester, et
> côté service Power BI après publication.

## Configuration du token GitHub (dépôt privé)

1. GitHub → *Settings → Developer settings → Fine-grained tokens → Generate*.
2. Portée : ce dépôt uniquement, permission **Contents : Read-only**.
3. Power BI Desktop : *Transformer les données → Gérer les paramètres* →
   coller le token dans `TokenGitHub`. Pour les identifiants de la source web,
   choisir **Anonyme** (le token transite par l'en-tête `Authorization`) et,
   si demandé, activer **Ignorer le test de connexion**.

## Actualisation automatique

```
scrape-daily.yml  ──►  generate-signals.yml  ──►  refresh-powerbi.yml
 (cours/dividendes)      (signaux + commit)         (déclenche le refresh)
        └──►  daily_update.yml (composition flottante)
```

`refresh-powerbi.yml` s'exécute après la génération des signaux et appelle
l'API REST Power BI (service principal Azure AD) pour lancer une actualisation
complète du jeu de données. Tant que les secrets ne sont pas configurés, l'étape
est ignorée proprement (le reste du pipeline n'est pas affecté).

### Secrets à configurer (Repo → Settings → Secrets and variables → Actions)

| Secret | Description |
|---|---|
| `PBI_TENANT_ID` | ID du tenant Azure AD |
| `PBI_CLIENT_ID` | ID de l'application (service principal) |
| `PBI_CLIENT_SECRET` | Secret de l'application |
| `PBI_GROUP_ID` | ID de l'espace de travail Power BI |
| `PBI_DATASET_ID` | ID du jeu de données publié |

Prérequis côté Power BI / Azure :

1. Créer une application Azure AD (service principal) + secret.
2. Dans le portail d'administration Power BI, autoriser les **principaux de
   service** à utiliser les API.
3. Ajouter le service principal comme **Membre/Contributeur** de l'espace de
   travail contenant le rapport.
4. Définir `TokenGitHub` dans les **paramètres du jeu de données** (service
   Power BI) et configurer les identifiants de la source Web sur **Anonyme**.

> Le refresh planifié natif de Power BI (Paramètres du dataset → Actualisation
> planifiée) reste une alternative ou un complément : la source étant 100 % web,
> il fonctionne sans passerelle.
