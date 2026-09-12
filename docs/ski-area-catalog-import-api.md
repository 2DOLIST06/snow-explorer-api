# Contrat front — import du catalogue des domaines

Le back lit exclusivement `data/imports/snow_explorer_domaines_import.json` (version
`snow-explorer-area-catalog/1`, maximum 2 Mio). Le navigateur n'envoie donc jamais le fichier.
Toutes les routes ci-dessous ont le préfixe `/api/admin/ski-area-catalog`, exigent le cookie de
session administrateur avec `credentials: "include"`; chaque `POST` exige en plus l'en-tête
`X-CSRF-Token` obtenu via `GET /api/admin/auth/session`. Les réponses sont JSON UTF-8, non mises
en cache. Les routes publiques ne retournent que les vrais liens `ski_area_resorts`.

## Import en deux étapes

### `POST /preview`

Corps absent ou `{}`. Analyse sans aucune écriture. Réponse `200` :

```json
{"preview":{"schema_version":"snow-explorer-area-catalog/1","catalog_id":"snow-explorer-fr-areas","batch_id":"2026-09-12-initial-verified","sha256":"…","size_bytes":168254,"non_exhaustive":true,"counts":{"areas_new":64,"areas_mapped":0,"area_collisions":0,"stations_matched":0,"stations_inactive":0,"stations_missing":0,"stations_optional_detail":28,"stations_candidates":0,"conflicts":0,"memberships_linkable":0,"memberships_pending":175},"areas":[{"catalog_key":"les-3-vallees","name":"Les 3 Vallées","proposed_slug":"les-3-vallees","state":"new","ski_area_id":null,"collision_ski_area_id":null}],"stations":[{"station_ref":"snow:courchevel","name":"Courchevel","state":"matched","resort_id":"…","is_active":true,"reason_or_candidates":"stable_id_and_slug"}],"review_proposals":[],"alerts":[]}}
```

Les valeurs de `state` station sont `matched`, `missing`, `candidate`, `optional_detail` ou
`conflict`; celles des domaines sont `new`, `mapped` ou `collision`. Une collision n'est jamais
fusionnée automatiquement.

### `POST /imports`

Corps : `{"expected_sha256":"empreinte renvoyée par preview"}`. L'empreinte est facultative mais
fortement recommandée. Le serveur relit et revalide les données courantes dans une transaction,
crée seulement les nouveaux domaines en `draft`, conserve toutes les attentes, et ne modifie ni
publication ni activation. Réponse `201` :

```json
{"import":{"id":"uuid","catalog_id":"snow-explorer-fr-areas","batch_id":"2026-09-12-initial-verified","status":"applied","sha256":"…","applied_at":"2026-09-12T15:00:00+00:00","result":{"areas_created":64,"areas_reused":0,"area_collisions":0,"stations_linked":83,"stations_pending":73,"memberships_created":175,"memberships_linked":94,"memberships_unchanged":0,"conflicts":[]}}}
```

`409 catalog_changed` impose une nouvelle prévisualisation. Un second import est additif et
idempotent : les décisions `ignored` et les contenus éditoriaux existants sont conservés.

### `GET /imports/{import_id}`

Réponse `200` : objet `import` précédent, complété par `schema_version`, `created_at`, et les objets
`preview` et `result` persistés. `404 import_not_found` si l'UUID n'existe pas.

## Attentes et rapprochement

### `GET /expectations`

Paramètres : `page` (défaut 1), `per_page` (défaut 25, maximum 100), `q` (nom ou `station_ref`),
`state` (`pending`, `needs_review`, `optional_detail`, `linked`, `ignored`). Réponse :

```json
{"items":[{"id":12,"catalog_id":"snow-explorer-fr-areas","station_ref":"fr:exemple","name":"Exemple","country_code":"FR","department":"Savoie","aliases":["Exemple village"],"origin_resolution":"missing","covered_by_resort_ids":[],"resort_id":null,"resolution_state":"pending","resolution_note":null,"expected_memberships":[{"id":19,"catalog_key":"domaine-exemple","ski_area_id":42,"area_name":"Domaine exemple","area_kind":"linked_area","notes":null,"sources":[{"publisher":"…","url":"https://…","publication_date":null,"checked_on":"2026-09-12"}],"evidence_status":"confirmed","relation_kind":"member_or_access_point","state":"pending","decision_origin":"catalog","decision_note":null}]}],"pagination":{"page":1,"per_page":25,"total":1,"pages":1}}
```

`GET /expectations/{id}` retourne `{"expectation": …}` ou `404 expectation_not_found`.

### `GET /stations/{resort_id}/candidates`

Retourne `{"station_id":"…","items":[{"expectation":…,"confidence":"strong|ambiguous","signals":["normalized_name","country","department"],"automatic_link":false}]}`.
Même `strong` reste une proposition : aucune ressemblance de nom n'autorise une liaison automatique.

### `POST /expectations/{id}/decision`

* confirmation : `{"decision":"confirm","resort_id":"uuid","note":"identité vérifiée"}`;
* refus : `{"decision":"ignore","note":"homonyme"}` (`resort_id` omis).

Réponse `200` : `{"expectation":…,"memberships_linked":2}`. Une confirmation associe l'identité
et crée en une transaction tous ses rattachements confirmés, sauf ceux précédemment ignorés. Un
refus est persistant. Erreurs : `400 validation_error`, `404 expectation_not_found` ou
`404 station_not_found`.

### `POST /reconcile`

Corps `{}`. Relance la recherche globale sans importer et sans lier sur le nom :
`{"stations_scanned":83,"proposals":[{"station_id":"…","expected_station_id":12,"confidence":"strong","signals":[…]}],"linked_automatically":0}`.

## Création d'une station depuis une attente

Le contrat existant `POST /api/admin/stations/` accepte désormais le champ supplémentaire
`station_ref`. Exemple complet :

```json
{"name":"Exemple","country_code":"FR","department":"Savoie","is_active":false,"station_ref":"fr:exemple"}
```

Le slug reste choisi/généré par le CRUD station; il n'est jamais dérivé de `station_ref`. La
création de la vraie station et la résolution de toutes ses relations sont atomiques. Réponses
existantes : `201 {"ok":true,"resort":…}`, `404` si `station_ref` est inconnu, `409` s'il est déjà
résolu. Une station créée sans `station_ref` reste indépendante; le front demande ensuite ses
candidats et fait confirmer l'identité par l'administrateur.

## Erreurs communes et cache

`401 {"error":"admin_authentication_required"}` signale une session absente/expirée;
`403 {"error":"csrf_validation_failed"}` un jeton absent/invalide; `400 invalid_catalog`,
`unsupported_catalog_version`, `catalog_too_large` ou `invalid_pagination` signalent une requête ou
un catalogue invalide. Les erreurs métier ont toujours `error` et `message`, et éventuellement
`details`. Aucun endpoint de ce document ne doit être mis en cache (`Cache-Control: no-store` peut
être ajouté par le proxy front). Après liaison, les pages publiques continuent d'utiliser
`ski_area_resorts`; aucune attente interne ne devient une URL publique.

## Déploiement base

Exécuter, dans l'ordre, `migrations/20260912_add_ski_areas.sql` si elle ne l'est pas déjà, puis
`migrations/20260913_add_ski_area_catalog_import.sql`. Cette tâche n'exécute aucune migration ni
aucun import en production.
