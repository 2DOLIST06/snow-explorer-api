# Snow Explorer Station Ops — SCAN et COMPARE

Cette couche backend extrait et examine les données existantes. Elle ne possède
aucune fonction d'écriture, de correction, de fusion ou d'APPLY.

## Endpoint et authentification

`GET /api/admin/station-ops/snapshot`

Le namespace admin est protégé par le hook central de l'application. Le backend
Flask actuel utilise le cookie opaque `admin_session`, dont le hash est vérifié
dans `admin_sessions`, avec compte actif, rôle admin, expiration, révocation et
invalidation après changement de mot de passe. Il n'utilise pas de JWT. Le serveur
Node historique possède un token statique, pas un JWT non plus. Le Dockerfile
démarre Flask/Gunicorn ; cette extension concerne cette application.

Station Ops reprend tous les contrôles existants. Seule la mise à jour
`last_seen_at` est omise sur les endpoints SCAN et COMPARE, également pour leurs méthodes refusées.
Les autres routes gardent leur comportement. OPTIONS ne retourne pas de données
et reste accessible pour CORS. HEAD exige la même authentification que GET.
La réponse porte `Cache-Control: no-store`.

## Filtres exacts

| Paramètre | Source | Format |
| --- | --- | --- |
| `id` | `resort.id` | chaîne exacte |
| `slug` | `resort.slug` | chaîne exacte |
| `country_code` | `resort.country_code` | chaîne exacte |
| `region_id` | `resort.region_id` | chaîne exacte, sans canonicalisation |
| `department` | `resort.department` | chaîne exacte |
| `is_active` | `resort.is_active` | `true` ou `false` |
| `ski_area_id` | `ski_area_resorts.ski_area_id` | entier bigint positif |

Les filtres se combinent par AND et sont paramétrés par Peewee. Un paramètre
inconnu, vide, répété ou invalide donne 400 `invalid_filters`. Un ID absent donne
un snapshot vide, avec des compteurs zéro effectivement calculés.

Sans filtre, toutes les stations sont scannées, actives ou inactives, y compris
celles qui n'ont pas de slug utilisable par les routes publiques. Avec filtre,
les compteurs et doublons concernent uniquement le sous-ensemble sélectionné.
Les champs `scope.summary` et `scope.duplicates` déclarent ce périmètre.

## Contrat versionné

- `schema_version`, `generated_at`, `scope` : version et contexte du scan.
- `summary` : nombres de stations totales, actives, inactives, avec au moins une
  error/warning, et nombre de paires de doublons potentiels. Une station peut
  avoir simultanément des errors et warnings ; info ne compte pas comme warning.
- `stations` : projection des valeurs stockées, données liées et findings.
- `ski_areas` : domaines effectivement liés aux stations sélectionnées, y
  compris les drafts, avec leurs valeurs propres et findings.
- `potential_duplicates` : paires d'IDs, classification, raisons et, le cas
  échéant, distance en mètres.
- `schema_findings` : écarts constatés entre modèles et colonnes physiques,
  séparés des constats de qualité des données des stations et de leurs compteurs
  `stations_with_errors` / `stations_with_warnings`.
- `catalog_findings` : diagnostics globaux non bloquants sur les catalogues de
  données, séparés des findings station et des écarts de schéma. Une table
  `regions` vide produit une seule entrée
  `{"code": "region_catalog_empty", "severity": "info", "table": "regions"}`.

Les `null`, chaînes vides, espaces, zéro et statuts sont préservés. Le slug n'est
jamais recalculé. Les quatre altitudes sont retournées indépendamment. Les
statistiques d'un domaine ne sont jamais copiées dans celles de ses stations.
Dates et timestamps sont sérialisés en ISO 8601 ; les montants sont des chaînes
décimales pour conserver leur précision. Un float non fini est représenté par
`{"non_finite": "..."}` plutôt que par un nombre JSON invalide.

Les clés de FK des dictionnaires Peewee (`resort`, `ski_area`, `season`, `product`,
`period`) contiennent leurs identifiants, jamais des objets chargés implicitement.

### Sources effectivement vérifiées dans le code

| Modèle | Table déclarée | Utilisation |
| --- | --- | --- |
| Resort | `resort` | identité, activation, géographie, statistiques, médias, contenu, saison |
| Region | `regions` | région liée par égalité de l'ID texte ; pas une FK de Resort |
| SkiArea | `ski_areas` | domaines, couleurs des pistes, snowparks, statistiques et saison |
| SkiAreaResort | `ski_area_resorts` | relation plusieurs-à-plusieurs, timestamps conservés |
| Piste | `piste` | vrais enregistrements de pistes et difficultés |
| Lift | `lift` | vrais enregistrements de remontées et types |
| ResortMap | `resortmap` | références des plans historiques |
| StationWidgets | `station_widgets` | configuration JSON texte liée par slug |
| SkiPassSeason | `ski_pass_seasons` | saisons tarifaires actives et inactives |
| SkiPassPeriod | `ski_pass_periods` | périodes tarifaires |
| SkiPassProduct | `ski_pass_products` | produits tarifaires |
| SkiPassPrice | `ski_pass_prices` | prix fixes/dynamiques et notes |

Le snapshot garde les champs de Resort effectivement présents en base, sauf le corps des sept contenus
éditoriaux, remplacé par `content.<champ>.{present,length,md5}`. LENGTH et MD5
sont calculés par PostgreSQL : ces corps ne sont pas transférés vers Python.
Les descriptions des régions et domaines suivent le même principe. MD5 sert
à une comparaison de contenu, jamais à une fonction de sécurité.

### Compatibilité avec le schéma physique

Au début de chaque transaction de scan, Station Ops inventorie les colonnes des
douze tables utilisées. PostgreSQL utilise un seul SELECT dans `pg_catalog`,
avec résolution des noms de relations par `to_regclass` et respect du search_path
ou du schema explicite du modèle. SQLite utilise les PRAGMA de lecture de Peewee.
L'inventaire n'est pas mis en cache entre scans et ne modifie jamais les modèles.

Les projections de toutes les tables sélectionnent uniquement les colonnes
physiquement disponibles. Les champs de contenu absents ne sont pas utilisés dans
LENGTH/MD5 et ne sont pas fabriqués dans `content`. Un champ optionnel absent du
schéma est omis, sans être assimilé à une valeur NULL stockée. Son absence donne
un diagnostic, par exemple :

```json
{
  "table": "regions",
  "field": "description_html",
  "column": "description_html",
  "code": "model_column_missing_in_database",
  "severity": "warning"
}
```

Les colonnes physiques absentes des modèles sont signalées par
`database_column_not_in_model` (info). Elles ne sont pas toutes exposées
automatiquement : cela évite de modifier le contrat des stations ou de diffuser
des colonnes inconnues. Pour les régions legacy, les colonnes vérifiées `slug`
et `created_at` sont exposées sous leur vrai nom, et `seo_text` est résumé sous
`region.content.seo_text` (présence, longueur, MD5). Aucun mapping n'affirme que
`seo_text` équivaut à `description_html`. Si les deux existent, ils sont distincts.

Une colonne d'identité ou de relation indispensable, une table indisponible, ou
la colonne d'un filtre demandé absente empêche une lecture sans ambiguïté.
Dans ce cas, l'endpoint retourne 503 `station_ops_schema_incompatible`, avec
`schema_findings` et un message explicite, avant les SELECT de données. Il
n'ignore pas le filtre et ne fabrique pas un snapshot complet avec des zéros.

Un entier Peewee stocké physiquement comme réel/numeric est lu sans conversion
en entier et signalé par `model_column_type_mismatch` (warning), pour ne pas
tronquer les kilomètres skiables. Un timestamp legacy sans fuseau correspondant
à UTCDateTimeField est signalé par le même code (info) et la convention explicite
`legacy_naive_assumed_utc` ; la normalisation UTC existante est conservée.

L'inventaire Render en lecture seule du 7 octobre 2026 a confirmé :

- `regions.description_html` absent ; `slug`, `seo_text`, `created_at` présents
  mais non déclarés par Region ; `updated_at` sans fuseau en base.
- `resort.ski_area_km` de type `real` pour un modèle entier ; les colonnes
  supplémentaires `openskimap_area_name` et `openskimap_enabled` sont signalées
  sans modifier le contrat des stations.
- Toutes les douze tables attendues sont présentes. Aucun autre champ déclaré
  par les modèles SCAN n'est absent des dix autres tables.

Cet inventaire décrit les noms de colonnes et les écarts de type traités ici ;
il ne remplace pas un audit exhaustif des contraintes, index ou conversions.

Les champs directs comprennent `id`, `name`, `slug`, `is_active`, `country_code`,
`region_id`, `region_name`, `department`, `latitude`, `longitude`,
`altitude_base_m`, `altitude_top_m`, `altitude_min_m`, `altitude_max_m`,
`ski_area_km`, `pistes_count`, `lifts_count`, `website_url`, `cover_image_url`,
`logo_url`, `amenities`, `meta_title`, `meta_description`, `page_layout_version`,
`pistes_small_map_url`, `pistes_large_map_url`, `pistes_caption`, `snowpark_map_url`,
`snowpark_caption`, `season_open_date`, `season_close_date`, `updated_at`.
Les sept contenus sont `description_md`, `description_html`, `v2_overview_html`,
`v2_weather_snow_html`, `v2_ski_pass_html`, `v2_piste_map_html`, `v2_webcam_html`.

`region` conserve le vrai enregistrement de Region s'il est trouvé, sans
remplacer `region_name` ou `country_code` stockés sur Resort.
`ski_area_links` conserve les relations, et `ski_areas` leurs références.
`pistes` et `lifts` conservent tous les enfants, leurs nombres d'enregistrements
et comptages par difficulté/type. Ces comptages n'affirment pas que le catalogue
enfants est exhaustif et ne remplacent pas les totaux stockés.

`widgets` reprend les blocs effectivement utilisés par le contrat public :
`pistes`, `meteo`, `description`, `forfaits`, `webcams`, `snow`, `snowpark`,
`remontees`, `snowparks`. Les wrappers historiques sont résolus avec le helper
existant, sans fabriquer de blocs absents. `widgets_state` distingue absent,
valid et invalid_json ; `widgets_sha256` décrit l'intégralité du texte stocké,
y compris les clés qui ne sont pas exposées. Les contenus HTML des widgets sont
remplacés par présence/longueur/SHA256. Leur colonne TEXT doit être lue pour
parser le JSON historique, qui peut être invalide ; aucun cast PostgreSQL fragile
en JSON n'est imposé. Les références HTTP sont conservées, sans téléchargement.
Les médias inline `data:` sont résumés, sans renvoyer leur payload.

`ski_pass_seasons` contient les saisons, périodes, produits et prix enregistrés.
`has_ski_pass_data` indique la présence d'au moins un prix normalisé ;
`has_legacy_forfait_items` indique la présence d'items dans le widget forfaits ;
`has_forfait_data` combine ces deux indicateurs. La présence ne vaut pas visibilité
publique : les flags enabled/is_active restent disponibles indépendamment.

Il n'existe pas de colonne commune/localité ni de table Country/Department dans
les modèles utilisés par la fiche. Le pays est un code et le département du texte.
Resort n'a pas de colonnes dédiées aux quatre couleurs, au nombre de snowparks,
à une webcam ou à un ski_area_id unique : leurs données viennent respectivement
des pistes/widgets, de `snowparks.count` ou `snowpark.count`, de `webcams.items`,
et des relations multiples. SkiArea a bien les compteurs par couleur et snowparks.

## Validation sans correction

`region_id`, `region_name` et `country_code` restent les valeurs brutes propres
à Resort. La table `regions` enrichit le snapshot lorsqu'un ID correspond à une
région disponible ; `region_name` n'est jamais utilisé pour fabriquer une relation.
Si le catalogue est globalement vide, `region` reste `null` et aucun warning
`region_not_found` n'est ajouté aux stations. Le diagnostic global
`region_catalog_empty` apparaît une seule fois dans `catalog_findings`, sans
compter dans `stations_with_warnings`.

Si le catalogue contient au moins une région, une station dont le `region_id`
est renseigné mais ne correspond à aucun enregistrement conserve le warning
`region_not_found`. Une région trouvée est exposée normalement. Un `region_id`
non renseigné ne déclenche pas ce contrôle. La présence du catalogue est vérifiée
dans son ensemble, indépendamment des filtres station, même si aucune station
ne correspond. Les autres findings continuent de déterminer normalement le
compteur `stations_with_warnings`.

Chaque finding contient `code`, `severity`, `field`.

- error : `missing_name`, `missing_slug`, `coordinates_invalid`,
  `altitude_inconsistent`, `negative_count`, `season_dates_inconsistent`.
- warning : `missing_coordinates`, `missing_country`, `missing_altitude`,
  `piste_total_inconsistent`, `widgets_invalid_json`, `invalid_count`,
  `region_not_found`.
- info : `missing_cover_image`, `missing_logo`, `missing_ski_area`,
  `missing_piste_data`, `missing_official_website`, `missing_trail_map`.

Les couples min/max et base/top sont vérifiés séparément. Le contrôle de
complétude des altitudes accepte le fallback public base/top sans modifier les
valeurs du snapshot. Les coordonnées doivent être finies et dans les plages
géographiques. Les couples de dates d'une même source sont comparés.

Un total de pistes n'est comparé à une somme que si les quatre compteurs de
couleur existent et sont des entiers non négatifs de la même source : SkiArea,
ou widgets de la station comparés au total station. Aucun total n'est confronté
à des enfants potentiellement partiels pour créer une fausse incohérence.
Les findings des domaines liés sont référencés dans ceux des stations avec un
chemin `ski_areas.<id>.<champ>`.

## Doublons potentiels

Critères : slug strictement identique, nom normalisé identique (casse, accents,
espaces et ponctuation), ou coordonnées distantes d'au plus 150 mètres.
La simple ressemblance textuelle ne suffit pas. Aucune paire n'est déclarée
certaine. Une paire répondant à plusieurs critères n'est comptée qu'une fois.
L'index géographique 3D fonctionne aux pôles et au passage de l'antiméridien.
Les groupes de noms/slugs sont indexés ; seule l'énumération de groupes réellement
ambigus peut générer beaucoup de paires. Rien n'est fusionné ou supprimé.

## Garanties de lecture seule

Le service prend la base liée au modèle Resort. Sur PostgreSQL, il ouvre une
transaction `REPEATABLE READ`, puis exécute `SET TRANSACTION READ ONLY` avant
toutes ses lectures. Une transaction préexistante est refusée pour ne pas affaiblir
cette garantie. Toute erreur interrompt le scan sans réponse partielle.
Le contexte SQLite des tests utilise `PRAGMA query_only=ON`, restauré en sortie.
Aucun service Station Ops n'appelle save/create/update/delete ni n'exécute de DDL.

Le nombre de SELECT PostgreSQL est fixe : douze lectures de données dans SCAN,
un inventaire du schéma, une vérification de présence du catalogue `regions`
par SELECT limité à une ligne, plus celui de l'authentification (quinze au total).
Cette vérification ne sélectionne aucun champ optionnel du modèle et reste dans
la même transaction READ ONLY ; elle fonctionne aussi sans `description_html`.
Les requêtes liées utilisent des sous-requêtes, sans liste géante de paramètres
ni chargement N+1. Le snapshot complet n'est pas paginé : à mesurer sur un grand
catalogue avant une future stratégie d'export ou pagination versionnée.

Les endpoints publics existants ne conviennent pas à cet audit : ils filtrent les
stations actives, certains transforment des valeurs ou appliquent des fallbacks.
Leur code et les règles métier existantes ne sont pas modifiés. L'extension
réutilise les modèles, le hook admin et le traitement des wrappers/widgets publics.

## Tests et limites d'intégration

`python -m unittest discover -s tests -p 'test_station_ops.py' -v`

Les fixtures utilisent PooledSqliteDatabase en mémoire, avec bind_ctx restauré,
et `SKIP_DATABASE_INIT=True`. Les connexions à la base PostgreSQL configurée
sont interdites par un mock. MD5 est enregistré seulement sur la fixture SQLite
pour tester la projection SQL utilisée par PostgreSQL. La transaction PostgreSQL
est vérifiée par mock, sans connexion ni DDL sur une base réelle.

Le rapport de livraison distingue les nouveaux tests des échecs historiques
reproduits sur HEAD dans les mêmes conditions isolées. Les chiffres d'exemple
proviennent des fixtures interrogées, jamais de la production.

Avant l'étape suivante : vérifier en lecture seule le schéma réellement déployé
et les performances, puis définir le contrat COMPARE/REVIEW, la provenance et la
politique de validation. Un orchestrateur externe devra utiliser le mécanisme de
session existant ou faire l'objet d'un chantier distinct d'authentification machine.
Le démarrage historique create_app crée des tables par défaut ; il n'a pas été
exécuté sur une vraie base pendant cette tâche et n'est pas modifié ici.

## Bilan de livraison initiale

Fichiers créés :

- `app/routes/admin_station_ops.py`
- `app/services/station_ops/__init__.py`
- `app/services/station_ops/scan.py`
- `app/services/station_ops/validation.py`
- `app/services/station_ops/duplicates.py`
- `tests/test_station_ops.py`
- `docs/station-ops-api.md`

Fichiers modifiés : `app/__init__.py` (enregistrement du blueprint et déclaration
de son endpoint en lecture seule) et `app/services/admin_auth.py` (option de
validation sans mise à jour d'activité, utilisée uniquement par cet endpoint).

Résultats dans l'environnement de livraison :

- 24 nouveaux tests Station Ops réussis sur SQLite en mémoire. Ils couvrent
  l'application complète avec sa protection admin, les refus d'accès et de
  méthodes, les données réelles des fixtures, tous les filtres, les incohérences,
  les relations multiples, les tarifs inactifs, les contenus volumineux, les
  doublons, l'absence d'écriture, le rollback et le nombre constant de SELECT.
- 9 tests pytest existants du cache public réussis.
- 275 tests unittest existants vérifiés par modules dans des processus séparés,
  avec connexions PostgreSQL interdites : 225 réussis, 44 failures et 6 errors.
  Les mêmes 50 tests échouent sur HEAD avant la modification, avec les mêmes
  identités de tests en échec. Aucun nouvel échec dans les tests existants.
  Plusieurs tests historiques utilisent encore la connexion PostgreSQL globale
  malgré leurs fixtures SQLite ; le garde-fou empêche ces accès. D'autres
  présentent des assertions ou fixtures déjà incompatibles avec le code ou Pillow.
- `git diff --check` réussi.

Extrait exact obtenu en interrogeant la fixture complète de Station Ops (aucun
chiffre de production, aucun chiffre supposé) :

```json
{
  "schema_version": "1.0",
  "scope": {
    "filters": {},
    "summary": "filtered_stations",
    "duplicates": "within_filtered_stations"
  },
  "summary": {
    "total_stations": 1,
    "active_stations": 1,
    "inactive_stations": 0,
    "stations_with_errors": 0,
    "stations_with_warnings": 0,
    "potential_duplicates": 0
  }
}
```

La réponse complète contient aussi `generated_at`, `stations`, `ski_areas` et
`potential_duplicates`. Le scan de cette fixture a exécuté exactement 13 SELECT,
authentification comprise.

Aucune base existante n'a été interrogée ou modifiée pendant cette tâche. Aucun
INSERT/UPDATE/DELETE sur une base existante, aucune migration ni modification de
schéma n'a été effectué. Les seules créations/modifications de données concernent
les fixtures SQLite éphémères explicitement isolées. Aucun modèle, frontend,
statut de station ou relation existante n'a été modifié. Aucun déploiement effectué.

## Correction du drift de schéma après le premier scan de production

La projection initiale se fiait aux champs Peewee et calculait LENGTH/MD5 sur
`Region.description_html` sans vérifier la table physique. Les fixtures modernes
créées avec le modèle masquaient cet écart legacy. La correction est locale à
Station Ops : nouveau helper `schema.py`, adaptation de `scan.py`, diagnostic
structuré dans `admin_station_ops.py`, tests et documentation. Aucun modèle,
endpoint public, frontend ou comportement métier existant n'est modifié.

La suite Station Ops comprend désormais 31 tests réussis, dont les 24 existants.
Les nouveaux tests utilisent une vraie table SQLite `regions` legacy avec les
neuf colonnes de production et sans `description_html`. Ils vérifient les données
legacy, le diagnostic, l'absence d'écriture et d'altération des modèles, la
préservation des kilomètres fractionnaires, l'inventaire à chaque scan, les
projections des autres tables, et le refus explicite de relations/filtres dont
une colonne essentielle est absente. Le SELECT PostgreSQL du catalogue est aussi
vérifié par mock et a été exécuté en lecture seule via le connecteur Render.
Les douze projections compilées ont également été validées sur PostgreSQL par
des SELECT avec LIMIT 0, sans lecture de lignes métier. Les 11 tests de cycle de
vie des connexions et les 9 tests de cache public réussissent aussi.

Seules des lectures de métadonnées et des requêtes de validation sans données
sont autorisées sur la production pour cette correction. Les tests écrivent
exclusivement dans leurs fixtures SQLite éphémères. Aucune migration, aucun DDL
ou DML en production, aucune modification de structure PostgreSQL. La correction est publiée uniquement sur une branche de PR, sans
fusion ni déploiement.

## Correction du bruit lié au catalogue de régions vide

La suite Station Ops passe à 35 tests réussis, avec quatre tests supplémentaires
sur le catalogue global vide, le catalogue non vide avec ID inconnu, les filtres
sans résultat, et le schéma legacy vide sans `description_html`. Le cas région
résolue vérifie également l'absence de diagnostic global et de warning.
Les fixtures vérifient `stations_with_warnings = 0` pour deux stations complètes
sans catalogue, puis `1` après ajout d'un vrai manque de coordonnées. Le cas ID
inconnu avec catalogue renseigné compte `1` station avec warning.

Les observations SQL pendant le scan et les comparaisons des données avant/après
confirment l'absence d'écriture, y compris sur la table legacy. Les 11 tests du
cycle de vie des connexions et les 9 tests de cache public réussissent aussi.
Les écritures de préparation des tests restent limitées aux fixtures SQLite
éphémères. Aucune requête de production, migration ou modification PostgreSQL
n'est nécessaire pour cette correction. La publication est limitée à une branche
de PR ; aucune fusion ni aucun déploiement n'est effectué.

## COMPARE — contrat 1.0

`POST /api/admin/station-ops/compare` reçoit des candidats sans télécharger ni
appeler le snapshot SCAN. Aucune recherche internet et aucune opération APPLY.
Cookie admin existant et header `X-CSRF-Token` obligatoires, comme sur les autres
POST admin. L'authentification conserve les contrôles d'expiration, révocation,
rôle et changement de mot de passe ; elle ne met pas à jour `last_seen_at`.
La réponse réussie est HTTP 200 avec `Cache-Control: no-store`, y compris si
certains candidats sont invalides. OPTIONS reste un préflight sans données.
Les autres méthodes sont refusées avec 405.

### Payload

L'enveloppe contient uniquement `candidates`. L'ordre des résultats correspond
à l'ordre d'entrée. Chaque candidat contient un `client_ref` non vide de 256
caractères maximum et un objet `data`. `client_ref` est renvoyé exactement, sans
trim ni déduplication ; sa valeur n'est jamais utilisée pour identifier une
station. `clear_fields` et `field_sources` sont facultatifs.

```json
{
  "candidates": [
    {
      "client_ref": "external-001",
      "data": {
        "id": "a",
        "altitude_max_m": 2550,
        "ski_areas": [{"id": 12}, {"slug": "other-area"}]
      },
      "clear_fields": ["website_url"],
      "field_sources": {
        "altitude_max_m": [
          {
            "url": "https://official.example/station",
            "source_type": "official",
            "observed_at": "2026-10-07T12:00:00Z"
          }
        ]
      }
    }
  ]
}
```

La définition canonique est interne à `candidates.py`. Aucun endpoint schema
supplémentaire n'est nécessaire à cette étape. Les champs scalaires autorisés
sont les suivants ; les champs et clés inconnus sont rejetés explicitement.

| Groupe | Champs |
|---|---|
| Identité et état | `id`, `slug`, `name`, `is_active`, `page_layout_version` |
| Géographie brute Resort | `region_id`, `region_name`, `country_code`, `department`, `latitude`, `longitude` |
| Statistiques | `altitude_base_m`, `altitude_top_m`, `altitude_min_m`, `altitude_max_m`, `lifts_count`, `pistes_count`, `ski_area_km` |
| Médias et contenus | `website_url`, `cover_image_url`, `logo_url`, `amenities`, `description_md`, `description_html`, `meta_title`, `meta_description` |
| Éditorial V2 | `v2_overview_html`, `v2_weather_snow_html`, `v2_ski_pass_html`, `v2_piste_map_html`, `v2_webcam_html` |
| Plans | `pistes_small_map_url`, `pistes_large_map_url`, `pistes_caption`, `snowpark_map_url`, `snowpark_caption` |
| Dates | `season_open_date`, `season_close_date` |

Les collections autorisées sont `ski_areas`, `pistes`, `lifts`, `maps`, `webcams`,
`ski_pass_seasons`, `ski_pass_periods`, `ski_pass_products`, `ski_pass_prices`
(listes d'objets) et `widgets` (objet). `updated_at` n'est pas une proposition
métier et n'est pas accepté. Aucun contenu de Region n'est candidat : ses données
legacy restent distinguées des valeurs propres à Resort.

### Absence, null et clear_fields

- Champ ou collection absent : aucune nouvelle information, aucun diff ou vidage.
- Valeur `null` : ignorée, avec `null_ignored` dans `validation.info` ; elle
  ne représente jamais une suppression implicite.
- Chaîne vide ou composée d'espaces dans un champ scalaire nullable : ignorée
  avec `blank_ignored`. Un ID, slug ou nom explicitement vide est invalide.
- `clear_fields` : liste des champs scalaires nullable à vider explicitement.
  Les cinq champs `id`, `name`, `slug`, `is_active`, `page_layout_version` et les
  collections sont exclus. La liste est dédupliquée. Une valeur non-null fournie
  simultanément pour un champ à vider rend le candidat invalide.
- Un clear d'une valeur déjà absente ne génère aucun changement. Aucune intention
  n'est appliquée, même si le résultat contient `cleared` ou une relation removed.

### Matching conservateur

Le moteur construit une fois par batch des index d'identité et de géographie,
avec des buckets spatiaux en coordonnées 3D, compatibles avec les pôles et
l'antiméridien. Les raisons sont factuelles et aucun score numérique de confiance
n'est retourné.

1. Un ID exact, littéral, existant est prioritaire. Des différences géographiques
   restent comparables comme modifications. Si le slug fourni désigne une autre
   station existante, `conflicting_station_identifiers` impose une revue.
2. Sans ID connu, les slugs exacts après trim sont examinés en priorité. Un slug
   unique est sûr si le pays concorde ou si des coordonnées proches le confirment,
   sans contradiction de pays/région/département ou coordonnées très éloignées.
3. Sinon, le nom normalisé (casse, accents, ponctuation, espaces) est rapproché
   avec la géographie. Un nom unique est sûr avec coordonnées proches, ou avec
   même pays et même région ou département. Les homonymes avec contexte
   géographique explicitement incompatible sont écartés, sauf confirmation de
   proximité, auquel cas la contradiction exige une revue.
4. Plusieurs stations plausibles au niveau retenu donnent `multiple_station_matches`
   et `multiple_matches`, jamais un choix arbitraire. Un rapprochement unique
   sans preuves suffisantes donne `insufficient_matching_evidence`.
5. Les coordonnées seules ne suffisent jamais : une proximité sans identité
   concordante donne `coordinates_only_match`. Aucune preuve plausible donne `new`.
6. Un ID fourni mais inconnu, alors qu'un autre rapprochement existe, exige une
   revue `candidate_id_not_found` pour éviter de substituer un identifiant.

Les 150 m de SCAN sont réutilisés comme confirmation positive, pas comme seuil
universel de rejet. Nom + pays + région peuvent encore identifier une station
à plusieurs kilomètres. Au-delà de 20 km entre deux points fournis, le matching
sans ID exact exige une revue `geographic_context_conflict`, avec
`coordinates_far_apart`. Ce seuil de prudence est documenté dans `matching.py` ;
il ne modifie aucune coordonnée ni relation.

### Normalisation et validation

Les valeurs d'entrée et la base restent inchangées. Le nom comparé par champ
ignore casse et espaces triviaux, mais conserve les accents : le matching peut
identifier une graphie sans accent tout en exposant son changement éditorial.
Les IDs sont littéraux, slugs et autres chaînes sont trimés, `country_code` est
comparé en majuscules. Région/département restent des identifiants textuels ;
aucun catalogue Region n'est nécessaire au matching des valeurs Resort.

Les nombres sont comparés avec Decimal, sans arrondi. Les nombres normalisés
sont sérialisés en chaînes décimales dans le diff. Les compteurs/altitudes
doivent être entiers ; kilomètres et coordonnées peuvent être fractionnaires.
Booléens acceptés : bool JSON, 0/1 entiers ou chaînes true/false/0/1 sans distinction
de casse. Dates : formats interprétables par `date.fromisoformat`, normalisés
en `YYYY-MM-DD`. URLs HTTP(S) absolues sans identifiants : schéma et hostname
en minuscules, domaine IDNA, port par défaut retiré, chemin racine normalisé.
Chemin, query, fragment et slash final non racine sont préservés. Aucun accès
réseau ne vérifie la cible. Les images inline `data:image/...` restent opaques.

Les types incompatibles, identité absente, coordonnées hors limites, dates/URLs
inexploitables, champs inconnus et provenance mal formée rendent le candidat
`invalid`. Un champ optionnel absent ne le rend pas invalide. Les incohérences
métier comparables (nombre négatif, altitudes inversées, saison inversée) restent
des warnings, avec réutilisation des vérifications objectives SCAN. Une coordonnée
isolée valide donne `partial_coordinates`, sans fabriquer l'autre valeur.
Les nombres doivent être finis avec exposant Decimal entre -308 et 308 ; cette
borne évite les représentations numériques démesurées dans la réponse.

### Statuts et réponse

Chaque candidat a exactement un statut. L'ordre de priorité après validation
est : `invalid`, puis `review_required` dès qu'une limitation/ambiguïté existe,
puis `new` sans match, ou `changes_detected` / `unchanged` pour un match sûr.
Pour `new`, les scalaires comparables fournis sont présentés comme added ; cela
ne constitue pas une autorisation ou une création de station.

```json
{
  "schema_version": "1.0",
  "compare_version": "1.0",
  "generated_at": "2026-10-07T12:00:00+00:00",
  "summary": {
    "total_candidates": 1,
    "new": 0,
    "unchanged": 0,
    "changes_detected": 1,
    "review_required": 0,
    "invalid": 0
  },
  "results": [
    {
      "client_ref": "external-001",
      "status": "changes_detected",
      "matched_station": {"id": "a", "slug": "alpha", "name": "Alpha"},
      "match_reasons": ["exact_id"],
      "changes": [
        {
          "field": "altitude_max_m",
          "existing": 2500,
          "candidate": 2550,
          "normalized_existing": "2500",
          "normalized_candidate": "2550",
          "change": "modified"
        }
      ],
      "validation": {"errors": [], "warnings": [], "info": []},
      "review_items": [],
      "field_sources": {}
    }
  ],
  "schema_findings": [],
  "catalog_findings": []
}
```

`changes` décrit `added`, `modified` ou `cleared`. Aucun diff pour une valeur
équivalente après normalisation. Les contenus éditoriaux complets ne sont ni
chargés ni renvoyés : LENGTH et MD5 sont sélectionnés uniquement pour les champs
fournis et les stations identifiées ; la valeur candidate est représentée par
`{"length": ..., "md5": ...}`. Le hash compare exactement le texte UTF-8 (sauf
les chaînes entièrement blanches ignorées). Ce mécanisme ne mesure pas une
équivalence sémantique HTML et une collision MD5 reste une limitation théorique.
Les images inline sont représentées dans le diff par longueur/SHA256.

`matched_station` est null pour new ou pour une identité ambiguë. Si l'identité
est sûre mais une collection demande revue, il reste renseigné et des diffs
scalaires sûrs peuvent être retournés, avec statut global `review_required`.
`match_distance_m` est facultatif pour un match avec deux points disponibles.
Chaque option de rapprochement en revue contient ID/slug/nom, raisons,
`conflicting_fields` et éventuellement `distance_m`.

Exemple de revue sans sélection arbitraire :

```json
{
  "code": "multiple_station_matches",
  "candidates": [
    {"id": "a", "slug": "alpha", "name": "Alpha", "match_reasons": ["exact_normalized_name", "same_country"], "conflicting_fields": []},
    {"id": "b", "slug": "other-alpha", "name": "Alpha", "match_reasons": ["exact_normalized_name", "same_country"], "conflicting_fields": []}
  ]
}
```

### Domaines et autres collections

`data.ski_areas` est une liste de références `{id}`, `{slug}` ou `{name}`, avec
combinaisons possibles. ID bigint positif ou slug exact unique permettent une
résolution sûre. Un ID/slug contradictoire ou inconnu et un nom seul donnent
`ski_area_reference_unresolved`, même si le nom n'a qu'une correspondance.
Les options de domaine connues sont renvoyées pour revue. Aucun domaine n'est
créé et aucun attribut de domaine n'est édité ; le nom est une aide à la revue.

Une liste fournie représente explicitement l'ensemble des relations proposées,
pas un ajout partiel. L'ordre et les doublons sont sans effet. Si toutes les
références et relations existantes sont résolues, le diff de `ski_areas` contient
les IDs existants/proposés et :

```json
{"relations": {"added": [3], "removed": [2], "unchanged": [1]}}
```

La liste vide représente une intention de retirer toutes les relations, sans
écriture ; une liste absente ou null ne retire rien. Une relation existante vers
un domaine introuvable impose `existing_ski_area_relation_unresolved`.

Les autres collections fournies (même vides) donnent un diagnostic
`collection_comparison_not_supported` avec le nom du champ et statut
`review_required`. Aucun diff potentiellement destructif n'est calculé, aucune
lecture de pistes/remontées/maps/widgets/forfaits n'est nécessaire. Leurs
structures internes attendront un contrat métier versionné ; seule la forme
liste d'objets / objet widgets est validée à cette étape.

### Provenance

`field_sources` mappe un champ canonique ou un chemin sous une collection à une
liste d'objets ayant `url` obligatoire, `source_type` textuel et `observed_at`
ISO datetime avec fuseau facultatifs. Les clés supplémentaires d'une source
ne sont pas acceptées. Les sources valides sont conservées telles que reçues,
y compris pour un champ inchangé. Aucune vérification internet ni persistance.

### Batch, schéma physique et lecture seule

Maximum : 1 000 candidats et 16 MiB de corps JSON. Cela autorise des lots de
catalogues nationaux avec des scalaires et sources tout en bornant mémoire,
CPU et taille de réponse ; les catalogues plus grands se découpent en lots.
Au-delà, HTTP 413 `invalid_compare_payload`. Enveloppe/JSON mal formé, JSON avec
clés dupliquées ou nombres non finis : HTTP 400. Content-Type non JSON : 415.
Une erreur propre à un candidat reste un résultat `invalid` dans un batch 200.
Les autres candidats continuent normalement.

Toutes les lectures métier sont faites sous REPEATABLE READ + SET TRANSACTION
READ ONLY PostgreSQL, avec le même garde-fou SQLite que SCAN dans les tests.
COMPARE ne contient aucun appel d'écriture. L'authentification conserve sa lecture
de session habituelle, sans touch. Un batch entièrement invalide ne lit pas les
données métier ; ses diagnostics globaux sont alors vides, sans affirmation
qu'un inventaire a été réalisé.

Un inventaire physique partagé des douze tables conserve `schema_findings`,
les colonnes supplémentaires et le type réel fractionnaire de ski_area_km. La
table regions vide produit `catalog_findings` exactement comme SCAN. Region
n'est lu que pour sa présence : pas de sélection de description_html absent,
pas de mapping de seo_text vers un champ modèle. Si son ID est indisponible,
`region_catalog_unavailable` indique la limitation globale.

Un champ candidat physiquement absent impose une revue
`field_unavailable_in_database`, sans supposer null ni produire un faux diff.
Si une colonne nécessaire à un rapprochement est indisponible et aucun ID
n'est résolu, `matching_columns_unavailable` empêche de conclure new. Un ID de
Resort indisponible empêche toute comparaison : HTTP 503
`station_ops_schema_incompatible` avec diagnostics. L'absence de colonnes
optionnelles de tables non utilisées n'interrompt pas les comparaisons scalaires.
Les domaines identifiés par ID restent comparables même sans colonne slug.

Lectures mutualisées, sans N+1 : un inventaire, un index limité aux huit champs
d'identité/géographie, une vérification du catalogue Region, au plus une lecture
des champs scalaires nécessaires pour les stations identifiées, et deux lectures
domaines/relations si ceux-ci sont fournis. Aucun snapshot complet ni gros
contenu global. Le maximum PostgreSQL est de 7 SELECT avec l'authentification,
indépendamment du nombre de candidats. L'index d'identité minimal lit le catalogue
Resort entier pour permettre les noms normalisés ; sa mémoire dépend du catalogue,
pas des contenus éditoriaux. Les cas très ambigus peuvent produire de grandes
listes d'options : aucune option plausible n'est arbitrairement supprimée.

### Vérification locale de COMPARE

- 79 tests Station Ops réussis : 35 SCAN conservés et 44 COMPARE, dont tous les
  statuts, absence/null/clear, provenance, domaines multiples, collections,
  schema drift, SQL read-only et non-écriture incluant l'authentification.
- Batchs de 500 et 1 000 candidats sur une fixture de 121 stations : 6 SELECT
  SQLite (auth incluse), plus 12 PRAGMA d'inventaire, comme pour un candidat.
  PostgreSQL ajoute le SELECT d'inventaire : maximum 7. Ce dernier chiffre est
  issu de la structure du moteur, pas d'une mesure live en production.
- 11 tests du cycle de vie des connexions et 9 tests du cache public réussis.
- 13 tests d'authentification réussis avec un harness SQLite isolant le hook de
  connexion de l'application. Exécution standard : 12 réussis et une erreur
  du test historique CORS/OPTIONS, qui essaie d'ouvrir PostgreSQL malgré
  SKIP_DATABASE_INIT. Aucun test historique n'a été modifié pour cette tâche.
- Transaction PostgreSQL vérifiée par mock : SET TRANSACTION READ ONLY exécuté
  avant le moteur, isolation REPEATABLE READ. Pas de validation live de COMPARE.

Aucune donnée, relation, migration ou structure PostgreSQL modifiée. Les
écritures de préparation des tests se limitent aux fixtures SQLite éphémères.
Aucun frontend ou endpoint public modifié. La publication est limitée à une
branche de PR, sans fusion ni déploiement. APPLY n'est pas développé.
