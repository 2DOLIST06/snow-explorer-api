# Administration des plans des pistes ANMSM

## Analyse de la source

Le flux configuré est la syndication française **Données Stations**
(`ANMSM_PISTE_MAPS_FEED_URL`). Sa valeur par défaut est la syndication Tourinsoft
`343718C6-9088-4732-AA05-26695D1E3059`, en JSON et impérativement avec
`refreshCache=0`.

La structure prise en charge correspond à celle déjà vérifiée par l'intégration
ANMSM : tableau racine (ou `value`, `items`, `results`), puis
`SyndicObjectID`, `SyndicObjectName`, et un objet `Object`. Le chemin documenté
est exactement `Object.PLANPISTESs[].Plandespistes`; sans enveloppe `Object`, le
parseur accepte `PLANPISTESs[].Plandespistes` à la racine. Chaque relation porte
le `SyndicObjectId` de la station. Les champs média lus sont `MediaID` (repli
`ID`), `Url`, `Extension`/`Format`, `Titre`, `Credit`,
`DateModification`, et uniquement `TypePlan`/`Type` pour le type. Le titre ne
sert jamais à déduire un type.

Les formats acceptés par sécurité sont JPEG, PNG, WebP et PDF. Les PDF sont
rendus par Poppler (`pdfinfo` puis première page avec `pdftoppm`) dans le même
processus enfant isolé que le décodeur Pillow, jamais dans Gunicorn. Le build
Docker installe explicitement `poppler-utils`. Les images produisent un WebP
haute définition, sans recadrage, sans agrandissement et en conservant
l'orientation et les proportions.

Le Blueprint `render.yaml` impose le runtime Docker. Le build vérifie
explicitement `pdfinfo` et `pdftoppm`, puis la commande de démarrage refait la
vérification avant Gunicorn et écoute le port fourni par Render (`$PORT`). Un
service Render historique configuré depuis le dashboard avec le runtime Python
natif doit être rattaché à ce Blueprint, ou converti manuellement en service
Docker : la seule présence du Dockerfile ne change pas son runtime.

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

La publication exige toujours `display_s3_key` et écrit son URL publique dans
`resort.pistes_large_map_url`. Le PDF original n'est donc jamais envoyé au modal.
La valeur précédente reste dans `previous_plan_url`/`previous_plan_s3_key` du
candidat. Chaque candidat est publié dans une transaction limitée à sa station.

La table n'est volontairement pas ajoutée à `create_tables`: exécuter
manuellement le script additif
`migrations/20260903_add_anmsm_piste_map_candidates.sql` dans DBeaver.
