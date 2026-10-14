# Préparation des fiches éditoriales de région

Ce correctif prépare les fiches utilisées par les stations. Il ne lance aucune
création au démarrage de l'API et ne modifie ni le schéma ni les stations.
L'exécution en production nécessite une autorisation séparée. Aucun `--apply`
n'a été exécuté en production pendant la préparation de ce correctif.

## Inventaire vérifié en lecture seule le 14 octobre 2026

Base Render : `snowexplorer-db-prod`. La table `regions` contient zéro ligne.
La dernière lecture contient 94 stations, toutes avec un `region_id` et un
`region_name` (contre 93 lors du premier audit ; aucune écriture de station
n'est effectuée par ce correctif).
Les six fiches éditoriales suivantes sont manquantes. ID et slug physique retenus
sont exactement l'identifiant déjà enregistré sur les stations, sans réaffectation.

| ID et slug retenus | Nom issu des stations | Pays | Stations | Actives |
| --- | --- | --- | ---: | ---: |
| auvergne-rhone-alpes | Auvergne-Rhône-Alpes | FR | 63 | 52 |
| bourgogne-franche-comte | Bourgogne-Franche-Comté | FR | 2 | 0 |
| grand-est | Grand Est | FR | 1 | 0 |
| nouvelle-aquitaine | Nouvelle-Aquitaine | FR | 1 | 0 |
| occitanie | Occitanie | FR | 9 | 7 |
| provence-alpes-cote-d-azur | Provence-Alpes-Côte d'Azur | FR | 16 | 14 |

`auvergne` reste l'identifiant historique de Le Lioran et Super-Besse (deux
stations inactives). Il est explicitement exclu de la préparation : aucune fiche
éditoriale `auvergne` n'est créée. Ces deux stations conservent intégralement
leurs `region_id`, noms, slugs et associations. Les six fiches représentent les
92 autres stations ; toutes les lignes de stations restent inchangées.

Les pages publiques frontend actuellement alimentées par des stations actives
sont `/regions/auvergne-rhone-alpes`, `/regions/occitanie` et
`/regions/provence-alpes-cote-d-azur`. Leurs identifiants et URL restent inchangés.
Les trois fiches sans station active seront accessibles dans l'administration
et via l'API de fiche ; leur création ne publie ni n'active de station et ne
garantit pas une page frontend publique (celle-ci exige des stations actives).

## Contraintes physiques PostgreSQL vérifiées

- `id`, `name`, `slug` : VARCHAR(255) NOT NULL, sans valeur par défaut.
- `country_code` : VARCHAR(2) NOT NULL, sans valeur par défaut.
- `created_at`, `updated_at` : TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  sans valeur par défaut ; le script fournit un horodatage UTC sans fuseau.
- `seo_text` : TEXT nullable ; `meta_title` : VARCHAR(70) nullable ;
  `meta_description` : VARCHAR(170) nullable.
- Clé primaire sur `id`, index unique `region_slug` sur `slug`.
- Trigger `regions_set_updated_at` uniquement BEFORE UPDATE sur `regions`.
- Aucune clé étrangère trouvée entre `regions` et les stations.

Le modèle Peewee n'expose pas les colonnes physiques `slug` et `created_at`.
L'insertion les fournit explicitement sans changer le modèle. Les routes
administratives cherchent par ID et exposent un slug égal à l'ID canonique.
La compatibilité PACA historique existante est conservée.

## Mécanisme idempotent

Depuis la racine du dépôt, avec la connexion de la base choisie :

```sh
python -m scripts.prepare_region_pages
```

Cette commande produit un JSON `mode: preview`, `missing`, `existing_ids`,
`excluded` et `inserted_ids: []`. `excluded` identifie notamment `auvergne`, avec
ses deux stations et la raison `not_an_editorial_region`. PostgreSQL utilise
une transaction REPEATABLE READ et READ
ONLY. Le script n'appelle pas `create_app` et n'initialise aucune table.

Seulement après validation du plan et autorisation de la base cible :

```sh
python -m scripts.prepare_region_pages --apply
```

La commande refait l'inventaire depuis les stations et limite les fiches aux six
identifiants éditoriaux explicitement retenus. Elle inclut les stations inactives
de ces régions et vérifie toutes les fiches avant d'insérer. Elle refuse les noms ou
pays contradictoires pour un ID, les identifiants invalides, les noms/pays
manquants et les collisions de slug. Une fiche existante (ou un alias PACA
existant) est conservée entièrement, même si son nom ou son contenu diffère.
Deux identifiants source concurrents ayant le même ID canonique nécessitent
une revue explicite ; le script ne choisit pas une fusion silencieuse.

Dans une transaction REPEATABLE READ, chaque fiche manquante reçoit cette
requête paramétrée, avec les valeurs de la table d'inventaire et la date UTC :

```sql
INSERT INTO "regions"
  ("id", "name", "slug", "country_code", "created_at", "updated_at",
   "seo_text", "meta_title", "meta_description")
VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, NULL)
ON CONFLICT ("id") DO NOTHING
RETURNING "id";
```

Avec l'inventaire actuel, cela représente six INSERT au maximum et un COMMIT.
Une nouvelle exécution insère zéro ligne. Un conflit tardif sur un ID ne
remplace pas la fiche ; toute autre erreur annule l'ensemble du lot. Il n'y a
aucun UPDATE, DELETE, ALTER TABLE, ni écriture sur `resort`. Aucun texte SEO
n'est généré et aucun contenu existant n'est écrasé.

Après une future exécution autorisée, purger le cache public avec le mécanisme
existant `POST /api/admin/cache/public/purge` (session admin + CSRF), pour éviter
qu'une ancienne liste publique vide reste en cache jusqu'à 24 heures. La liste
administrative n'est pas mise en cache. La purge n'est pas exécutée par le script.

## Sauvegarde et limites de périmètre

Le [contrat GET/PATCH](regions-api.md) conserve `description_html`, stocké dans
`seo_text`, et les métadonnées. L'alias administratif `seo_text` rend le formulaire
actuel compatible sans modification frontend. Aucun endpoint de création public
ou administratif supplémentaire n'est ajouté.

`GET /api/regions` expose désormais explicitement `id` et `slug` canoniques,
ainsi que `meta_title`, `meta_description` et `description_html`. Le frontend
existant peut choisir la fiche par son slug avant d'utiliser son nom et récupérer
les champs SEO depuis la liste qu'il consomme déjà. Il n'y a plus de deuxième
fiche Auvergne-Rhône-Alpes dans le plan. Une éventuelle fiche historique déjà
présente est conservée ; son slug explicite empêche de la sélectionner pour
`auvergne-rhone-alpes` par simple homonymie.

Le HTML autorisé reste inchangé (H2/H3, strong/em, listes, liens HTTP/HTTPS,
paragraphes et citations). Le rendu frontend public existant retire les balises
HTML ; cette limite frontend n'est pas modifiée par ce correctif.

## Aperçu et séparation des opérations

Le déploiement du backend rend le code disponible, mais ne lance pas le script.
L'exécution du script est une opération distincte ; l'aperçu n'insère rien.
Le mode `--apply` et le déploiement restent soumis à une autorisation explicite.
La validation locale utilise SQLite avec les contraintes physiques reproduites.
En l'absence de connexion PostgreSQL directe configurée dans l'environnement,
l'aperçu peut être exécuté sur une copie en mémoire alimentée par un SELECT
Render en lecture seule ; cela doit être distingué d'une exécution directe en
production. L'inventaire et les contraintes PostgreSQL sont lus via Render.
