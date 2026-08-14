# Dates publiques utilisées par le sitemap

## État du schéma et choix retenu

L'inspection des modèles `resort`, `station_widgets` et `regions` n'a révélé
aucun champ de création ou de modification existant. La migration
`20260814_add_public_content_timestamps.sql` ajoute donc :

- `resort.created_at` et `resort.updated_at` ;
- `station_widgets.updated_at` ;
- `regions.created_at` et `regions.updated_at`.

Tous les champs sont des `TIMESTAMPTZ`. L'API les sérialise en ISO 8601 UTC,
avec le suffixe `Z` (par exemple `2026-08-14T06:00:00Z`). Une absence de date
fiable est exposée par `null`, jamais par la date de la requête.

## Initialisation des données historiques

La migration laisse explicitement ces colonnes à `NULL` sur les lignes déjà
présentes. Il serait trompeur d'utiliser la date de déploiement comme date de
création ou de dernière modification. La première modification réelle après
migration renseigne `updated_at`; les nouvelles lignes reçoivent leurs dates à
l'insertion. `created_at` reste `NULL` pour une ancienne ligne dont la date de
création demeure inconnue.

Des triggers PostgreSQL constituent la garantie centrale, y compris pour une
écriture effectuée hors de l'API. Une lecture ou une sauvegarde sans changement
de contenu conserve les dates existantes. Le trigger de `station_widgets` ne
réagit qu'à un changement réel de `config` (ou de son rattachement à la station).

## Dates consolidées exposées

- Les réponses publiques de liste et de détail d'une station exposent
  `updated_at = MAX(resort.updated_at, station_widgets.updated_at)` ainsi que
  `created_at` lorsqu'il est connu.
- Les régions sont stockées dans la table `regions`, avec leur propre contenu
  éditorial. Leur `updated_at` public est le maximum entre cette date éditoriale
  et les dates consolidées de leurs stations publiques actives. La liste
  `/api/regions` et le détail `/api/regions/{slug}` l'exposent.
- Ajouter, activer, désactiver ou modifier une station active peut ainsi faire
  évoluer la page région. Une modification de widgets publics fait également
  évoluer la station et sa région.

Les pages statiques (`/`, `/stations`, `/meteo`, `/forfaits`, `/contact`) ne
créent aucune donnée en base et restent sous la responsabilité du frontend.
