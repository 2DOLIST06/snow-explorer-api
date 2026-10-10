# catalog_schema — inventaire dynamique et taux de remplissage

Outil ajouté au MCP existant `/mcp/station-ops`, scope `station-ops:read`,
`readOnlyHint=true`, `destructiveHint=false`. Aucun nouvel OAuth, serveur,
plugin ou migration. Les outils SCAN / COMPARE / REVIEW / APPLY restent inchangés.
`station_catalog` et les sept autres outils actuels sont conservés.

## Utilisation

| Demande | Arguments MCP |
| --- | --- |
| Donne-moi tous les champs existants des stations françaises actives. | `{"entity":"station","country_code":"FR","is_active":true,"min_fill_rate":0}` |
| Donne-moi tous les champs remplis sur au moins 10 % des stations françaises actives. | `{"entity":"station","country_code":"FR","is_active":true,"min_fill_rate":0.1}` |
| Donne-moi tous les champs remplis sur au moins 90 % des stations françaises actives. | `{"entity":"station","country_code":"FR","is_active":true,"min_fill_rate":0.9}` |
| Donne-moi les champs présents entre 10 % et 90 % des stations actives. | `{"entity":"station","is_active":true,"min_fill_rate":0.1,"max_fill_rate":0.9}` |
| Donne-moi tous les champs de domaine skiable existants. | `{"entity":"ski_area"}` |

Valeurs par défaut : `entity=station`, `min_fill_rate=0`, `max_fill_rate=1`,
`include_nested=true`. Les bornes sont inclusives et doivent être comprises entre
0 et 1 ; une borne minimale supérieure à la maximale est rejetée. `is_active`
est un booléen JSON. `include_nested=false` garde uniquement les chemins racines.
Les filtres de pays et d'activité sur les domaines sélectionnent les domaines
liés à au moins une station correspondant aux deux critères. Sans ces filtres,
les domaines sans station sont inclus.

## Architecture et découverte

Le service `app/services/station_ops/catalog_schema.py` utilise la transaction
REPEATABLE READ / READ ONLY existante (SQLite `query_only` dans les fixtures).
`PhysicalSchema` interroge le catalogue PostgreSQL pour les tables prises en
charge par Station Ops (`SCAN_MODELS`). Une seconde requête groupée récupère la
nullabilité physique. Chaque table est ensuite lue une seule fois, en projetant
**toutes les colonnes physiques**, y compris celles absentes des modèles Peewee.
Il n'existe aucune liste de champs à maintenir dans cet outil ou dans le plugin.

Les relations et leurs noms proviennent des ForeignKeyField/backrefs des modèles.
Des index en mémoire relient les lignes ; aucun accès ORM lazy par station.
Les références complètes sont exposées sous `<foreign_key>_record` et la traversée
s'arrête lorsqu'un modèle est déjà présent sur le chemin, pour éviter les cycles.
Les saisons, périodes, produits et prix sont donc disponibles même si toutes les
collections de la population sont vides. Les widgets sont rattachés par leur slug,
leur JSON déroulé avec la sémantique existante `widgets`/`cfg`, sans filtre de clés.
Les colonnes JSON natives, `config`, `*_json` et tout texte représentant un
conteneur JSON valide sont décodés. Les colonnes de stockage des widgets sont
également découvertes sous `station_widgets` ; la région est rattachée par son identifiant. Les JSON invalides restent du texte stocké, sans valeur inventée.

L'inventaire est découvert sur tout le catalogue, puis les statistiques sont
calculées sur la population filtrée. Ainsi un champ JSON connu uniquement sur une
station étrangère reste visible avec un taux nul dans le profil français.
Une nouvelle colonne physique ou clé JSON est découverte automatiquement.
Une nouvelle table hors du périmètre pris en charge par Station Ops nécessite
son intégration au périmètre des modèles. Une clé JSON jamais stockée et sans
modèle déclaratif ne peut pas être devinée.

## Métadonnées

- `type` : type SQL normalisé pour les colonnes, inféré pour les chemins JSON.
  Les types JSON hétérogènes sont joints par `|` et triés ; `unknown` si uniquement null.
- `nullable` : nullabilité SQL réelle ; true pour les chemins JSON facultatifs.
- `collection` : true pour les tableaux et relations multiples.
- `internal` : identités, clés étrangères, colonnes préfixées `_`, timestamps `*_at`.
  Ces champs restent visibles ; ce marqueur n'est pas une autorisation.
- `readable` : true pour les champs de ce périmètre métier pris en charge.
- `writable` : autorisation du moteur Station Ops APPLY actuel, reprise directement
  de `SCALAR_WRITE_FIELDS` pour les colonnes station. Les autres chemins sont false.
  Cette propriété décrit la capacité existante : catalog_schema ne permet aucune écriture.

## Remplissage

`null`, chaîne vide ou uniquement blanche, objet vide et tableau vide ne sont pas
remplis. `0` et `false` sont remplis. Un objet ou tableau contenant une donnée
remplie est rempli ; une structure non vide dont toutes les feuilles sont vides
ne l'est pas. Cette règle récursive évite les faux positifs des widgets coquilles.
Chaque sous-champ est également évalué individuellement. Les métadonnées de
stockage (par exemple un identifiant) restent évaluées comme données stockées ;
un widget comprenant un booléen false a une vraie valeur selon ces règles.

Pour chaque chemin, `filled_count` compte les **entités racines** possédant au
moins une valeur remplie à ce chemin. Quatre pistes avec un nom sur une station
comptent pour une station, pas quatre. `total_count` est toujours le nombre de
stations/domaines filtrés, y compris ceux sans collection. `fill_rate` est
`filled_count / total_count`, arrondi à quatre décimales ; 0 si la population est
vide. Les filtres s'appliquent au ratio exact avant arrondi. Les chemins sont triés.

## Performance et validation

Le nombre de requêtes est indépendant du nombre de champs et de stations :
deux lectures groupées de métadonnées PostgreSQL et au plus une lecture par table,
plus la gestion de transaction. SQLite utilise des PRAGMA par table dans les tests.
Le service ne renvoie aucune valeur du catalogue, uniquement des métadonnées et
compteurs. La mémoire dépend du volume du catalogue et des relations développées.

`tests/test_catalog_schema.py` couvre filtres, bornes, collections, forfaits,
widgets, domaines, 0/false, structures vides, autorisation OAuth et garde SQL.
Il prouve l'évolutivité via une nouvelle colonne SQL absente du modèle et une
nouvelle clé JSON, sans modification de l'implémentation. Les tests MCP/OAuth
existants vérifient la découverte de neuf outils au lieu de huit.

## Réponses exécutées, provenance et limites

Les fichiers [fixture-0.json](examples/catalog-schema/fixture-0.json),
[fixture-0-1.json](examples/catalog-schema/fixture-0-1.json) et
[fixture-0-9.json](examples/catalog-schema/fixture-0-9.json) sont des réponses
réellement produites par le service sur les fixtures SQLite isolées : dix stations
françaises actives, dont Alpha possède les données détaillées. Ce ne sont pas
les données de production.

| min_fill_rate | Population | Champs retournés | name | logo_url | snowpark_caption |
| --- | --- | --- | --- | --- | --- |
| 0 | 10 | 185 | 1 | 0.1 | 0 |
| 0.1 | 10 | 80 | 1 | 0.1 | exclu |
| 0.9 | 10 | 7 | 1 | exclu | exclu |

[local-postgresql-empty.json](examples/catalog-schema/local-postgresql-empty.json)
contient les trois exécutions sur PostgreSQL local `ski`. Cette base contient
zéro station : 169 chemins découverts au seuil 0, aucun au seuil 0.1 ou 0.9.
Aucune mesure du taux de remplissage de la production n'est revendiquée.

## Validation finale

Suite complète exécutée après intégration de main `ccb7c99` (station_catalog) :
`608 passed, 24 failed, 197 subtests passed`. Le même lancement sur main sans
cette modification donne `578 passed, 24 failed, 197 subtests passed`.
Les 24 identifiants de tests en échec sont strictement identiques : aucune
régression supplémentaire. Les 30 nouveaux tests catalog_schema passent,
y compris le profil de 1 000 stations avec 12 SELECT de données.

Commande utilisée : `python -m pytest tests -q --disable-warnings`.
PostgreSQL local a également exécuté les trois profils sans aucune écriture métier.
