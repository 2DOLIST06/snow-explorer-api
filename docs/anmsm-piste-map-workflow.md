# Administration des plans des pistes ANMSM

## Analyse de la source

Le flux configuré est la syndication **Espace neige** déjà utilisée pour les
stations (`ANMSM_PISTE_MAPS_FEED_URL`, avec repli sur `ANMSM_STATIONS_FEED_URL`).
La valeur historique est la syndication Tourinsoft
`343718C6-9088-4732-AA05-26695D1E3059`, en JSON et impérativement avec
`refreshCache=0`. Le proxy réseau de l'environnement de développement a refusé
la requête d'inspection le 3 septembre 2026 (HTTP 403 avant Tourinsoft). Il
n'était donc pas honnête d'affirmer avoir observé des formats ou un champ
« Média » distinct. Aucune récupération de production n'a été lancée.

La structure prise en charge correspond à celle déjà vérifiée par l'intégration
ANMSM : tableau racine (ou `value`, `items`, `results`), puis
`SyndicObjectID`, `SyndicObjectName`, et un objet `Object`. Dans celui-ci,
`NOM` porte le nom; les colonnes média explicitement exportées
`PLANPISTES`/`PLAN_DES_PISTES` contiennent une liste de médias. Les champs lus
sont `MediaID` (repli `ID`), `Url`, `Extension`/`Format`, `Titre`, `Credit`,
`DateModification`, et uniquement `TypePlan`/`Type` pour le type. Le titre ne
sert jamais à déduire un type. `ANMSM_PISTE_MAP_FIELDS` permet d'indiquer les
noms de colonnes confirmés sans changer le code.

Les formats acceptés par sécurité sont JPEG, PNG, WebP et PDF. **Aucun format
réellement rencontré n'a pu être certifié dans cet environnement**. Un PDF est
conservé comme original, mais n'est pas rendu avant inspection réelle du flux;
il n'est donc pas publiable dans le modal image. Les images produisent un WebP
haute définition, sans recadrage, sans agrandissement et en conservant
l'orientation et les proportions.

## Modèle public et exploitation

Le modal public consomme le champ canonique `resort.pistes_large_map_url`. La
publication ne transforme pas le site en galerie. Elle conserve la valeur
précédente dans le candidat, met à jour ce seul champ et invalide uniquement la
clé Redis de la fiche station. Les correspondances proviennent exclusivement de
`station_external_mappings` (`source='anmsm'`).

Routes :

* `GET /api/admin/anmsm/piste-maps/workspace` (métadonnées seulement);
* `POST /api/admin/anmsm/piste-maps/prepare` (exactement un média);
* `POST /api/admin/anmsm/piste-maps/bulk-approve` (transactions par candidat/station);
* les associations manuelles restent sur `POST /api/admin/anmsm/station-mappings/confirm`.

Les objets sont immuables sous `anmsm/piste-maps/<station>/<media>/<sha256>/`.
Original et éventuel WebP d'affichage sont conservés. Les clés seules sont
persistées; les aperçus privés sont présignés à la demande. Aucun chemin de ce
workflow n'appelle une suppression S3.

La table n'est volontairement pas ajoutée à `create_tables`: exécuter
manuellement le script additif
`migrations/20260903_add_anmsm_piste_map_candidates.sql` dans DBeaver.
