# Workflow d’administration des logos ANMSM

Toutes les routes ci-dessous sont sous `/api/admin`, utilisent la session admin,
le jeton CSRF pour les écritures et le CORS admin commun. `OPTIONS` est traité par
Flask-CORS sans authentification.

## Contrat frontend

1. `GET /api/admin/anmsm/logos/workspace` récupère uniquement le JSON
   Tourinsoft et renvoie `{ok, rows, stats}`. Chaque ligne contient les champs
   `external_station_id`, `anmsm_station_name`, `anmsm_media_id`, `anmsm_title`,
   `anmsm_credit`, `source_url`, `source_has_logo`, `mapping_status`,
   `mapping_method`, `station_id`, `station_name`, `current_logo_url`,
   `suggestion`, `candidate_id`, `candidate_status`, `candidate_preview_url`,
   `candidate_size_bytes`, `candidate_width`, `candidate_height`, `warnings`,
   `preparation_required` et `preparation_error`. Aucun média n’est téléchargé.
2. Le frontend appelle **séquentiellement**
   `POST /api/admin/anmsm/logos/prepare` avec
   `{"external_station_id":"STATANMSM…"}` pour les lignes marquées
   `preparation_required`. La réponse est
   `{"ok":true,"unchanged":false,"candidate":{…}}`. Une reprise retourne le
   candidat existant avec `unchanged: true`. Une erreur retourne toujours
   `{ok:false,error,message,external_station_id}`.
3. Une association manuelle utilise exclusivement
   `POST /api/admin/anmsm/station-mappings/confirm` et
   `{"mappings":[{"external_station_id":"…","station_id":"…"}]}`. Les
   résultats conservent l’index, les deux identifiants et la ligne workspace
   actualisée. Un élément incomplet renvoie `invalid_indexes` avec HTTP 400.
4. `POST /api/admin/anmsm/logos/bulk-approve` reçoit uniquement
   `{"candidate_ids":[123,124]}`. Il renvoie `{ok, approved_count,
   failed_count, results}`. Chaque résultat contient `candidate_id`, `ok`, puis
   `status`, `station_id`, `published_logo_url` en cas de succès, ou `error` en
   cas d’échec. Chaque identifiant est validé dans sa propre transaction.

Pour un bucket privé (`AWS_S3_PRIVATE=true`), les aperçus sont présignés au
moment de la réponse. Seule la clé stable est utilisée comme source de vérité ;
une URL présignée n’est jamais écrite en base.

## Compatibilité

`GET /logos`, `GET /logos/selection`, `POST /logos/sync` et
`POST /logos/<id>/approve` restent disponibles et sont marqués dépréciés.
`sync` est borné à une station ; aucune route ne lance une conversion globale.

## Stockage et publication

Le champ canonique publié reste `resort.logo_url`. Une validation copie sa
valeur précédente dans `station_logo_candidates.previous_logo_url` et, si elle
correspond au bucket configuré, sa clé dans `previous_logo_s3_key`. Aucun appel
S3 de suppression n’est effectué. Les tables et colonnes additives nécessaires
existent déjà dans `migrations/20260903_add_anmsm_logo_candidates.sql`; aucune
nouvelle requête SQL n’est nécessaire pour cette évolution.
