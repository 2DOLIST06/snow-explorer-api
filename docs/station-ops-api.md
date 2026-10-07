# Snow Explorer Station Ops — SCAN

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
`last_seen_at` est omise sur ce endpoint, également pour ses méthodes refusées.
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
