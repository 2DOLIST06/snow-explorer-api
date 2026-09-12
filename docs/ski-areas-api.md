# Contrat API — domaines skiables

> Tous les objets et valeurs JSON ci-dessous sont des **données fictives de démonstration**.

## Modèle, unités et médias

Les stations restent dans la table historique `resort`. `ski_areas` contient les domaines et
`ski_area_resorts` leur relation plusieurs-à-plusieurs (unicité `(ski_area_id, resort_id)`). Aucune
donnée station n'est copiée, additionnée ou déduite. Il n'y a pas de hiérarchie dans cette version.

| Champ | Type | Obligatoire | Description |
|---|---|---:|---|
| `id` | entier 64 bits | réponse | Identifiant généré |
| `name`, `slug` | chaîne | création | Nom; slug unique `[a-z0-9]+(-[a-z0-9]+)*` |
| `status` | `draft` ou `published` | non | Défaut `draft` |
| `description` | chaîne/null | non | Texte éditorial |
| `cover_image_url`, `piste_map_url` | chaîne URL/null | non | URLs publiques issues du stockage média existant (`POST /api/s3/presign`) |
| `altitude_min_m`, `altitude_max_m` | entier ≥ 0/null | non | Mètres; minimum ≤ maximum |
| `ski_area_km` | entier ≥ 0/null | non | Kilomètres de pistes |
| `pistes_count`, `green_pistes_count`, `blue_pistes_count`, `red_pistes_count`, `black_pistes_count`, `lifts_count` | entier ≥ 0/null | non | Comptages |
| `forecast_open_date`, `forecast_close_date` | `YYYY-MM-DD`/null | non | Dates prévisionnelles; ouverture ≤ fermeture |
| `season` | chaîne/null | non | Libellé, par ex. `2026-2027` |
| `source` | chaîne/null | non | **Administration seulement** |
| `verified_at` | datetime ISO 8601/null | non | **Administration seulement**, ex. `2026-09-12T10:00:00Z` |
| `created_at`, `updated_at` | datetime ISO 8601 | réponse admin | Horodatages |

Un zéro numérique est conservé et signifie « zéro renseigné »; `null` signifie « inconnu ». Sur
`PATCH`, un champ omis est conservé. Pour un champ facultatif, `null` l'efface; `""` efface aussi
les textes et dates. `name` et `slug` ne peuvent être ni `null` ni vides. Les clés inconnues sont
refusées. `station_ids`, lorsqu'il est fourni à la création ou au `PATCH`, remplace exactement les
rattachements; omis, il ne les modifie pas.

## Authentification administrative

Toutes les routes `/api/admin/*` utilisent la session existante: connexion par
`POST /api/admin/auth/login`, cookie HttpOnly envoyé avec `credentials: "include"`, puis en-tête
`X-CSRF-Token` sur `POST`, `PUT`, `PATCH`, `DELETE`. Une session absente/invalide produit
`401 {"error":"admin_authentication_required"}` et un CSRF absent/invalide
`403 {"error":"csrf_validation_failed"}`. Les routes publiques n'acceptent pas les brouillons.

## Routes administratives

* `GET /api/admin/ski-areas?page=1&per_page=20&q=alpes&status=draft`: recherche insensible à la
  casse dans nom/slug, tri nom puis id; `per_page` 1–100.
* `GET /api/admin/ski-areas/{id}`: fiche complète et `stations`.
* `POST /api/admin/ski-areas`: création transactionnelle.
* `PATCH /api/admin/ski-areas/{id}`: modification partielle transactionnelle.
* `POST /api/admin/ski-areas/{id}/publish` et `/unpublish`: change seulement le statut.
* `POST /api/admin/ski-areas/{id}/stations/{station_id}`: ajoute une relation (201).
* `DELETE /api/admin/ski-areas/{id}/stations/{station_id}`: retire seulement cette relation (204).
* `GET /api/admin/stations/{station_id}/ski-areas`: domaines d'une station.
* `PUT /api/admin/stations/{station_id}/ski-areas` avec `ski_area_ids`: remplace transactionnellement
  les domaines de cette station, sans toucher aux relations des autres stations.
* `GET /api/admin/ski-areas/station-options?page=1&per_page=50&q=val`: sélecteur paginé des stations.

Création fictive complète:

```http
POST /api/admin/ski-areas
Content-Type: application/json
X-CSRF-Token: demonstration-csrf

{"name":"Grand Domaine Démo","slug":"grand-domaine-demo","status":"draft",
 "description":"Description fictive","cover_image_url":"https://cdn.example.test/area.jpg",
 "piste_map_url":"https://cdn.example.test/map.webp","altitude_min_m":1100,
 "altitude_max_m":2800,"ski_area_km":150,"pistes_count":80,"green_pistes_count":10,
 "blue_pistes_count":30,"red_pistes_count":30,"black_pistes_count":10,"lifts_count":35,
 "forecast_open_date":"2026-12-05","forecast_close_date":"2027-04-18",
 "season":"2026-2027","source":"https://source.example.test","verified_at":"2026-09-12T10:00:00Z",
 "station_ids":["station-demo-a","station-demo-b"]}
```

```json
{"ski_area":{"id":42,"name":"Grand Domaine Démo","slug":"grand-domaine-demo","status":"draft",
"description":"Description fictive","cover_image_url":"https://cdn.example.test/area.jpg",
"piste_map_url":"https://cdn.example.test/map.webp","altitude_min_m":1100,"altitude_max_m":2800,
"ski_area_km":150,"pistes_count":80,"green_pistes_count":10,"blue_pistes_count":30,
"red_pistes_count":30,"black_pistes_count":10,"lifts_count":35,
"forecast_open_date":"2026-12-05","forecast_close_date":"2027-04-18","season":"2026-2027",
"updated_at":"2026-09-12T10:00:00+00:00","source":"https://source.example.test",
"verified_at":"2026-09-12T10:00:00+00:00","created_at":"2026-09-12T10:00:00+00:00",
"stations":[{"id":"station-demo-a","name":"Station A","slug":"station-a",
"cover_image_url":null,"logo_url":"https://cdn.example.test/logo-a.svg"}]}}
```

Effacement fictif sans modifier les autres champs: `PATCH /api/admin/ski-areas/42` avec
`{"description":null,"lifts_count":null}`. Publication: `POST /api/admin/ski-areas/42/publish` sans
corps. Remplacement côté station: `PUT /api/admin/stations/station-demo-a/ski-areas` avec
`{"ski_area_ids":[42,43]}`. Les réponses GET/PUT station sont
`{"station":{...},"ski_areas":[{...}]}`; ajout unitaire: `{"ski_area_id":42,"station_id":"station-demo-a"}`.

Les listes répondent `{"items":[...],"pagination":{"page":1,"per_page":20,"total":1,"pages":1}}`.

## Routes publiques

* `GET /api/ski-areas?page=1&per_page=20`: domaines publiés; parcourir `page` jusqu'à `pages` pour
  construire le sitemap (maximum 100 par page).
* `GET /api/ski-areas/{slug}`: domaine publié avec ses seules stations publiées.
* `GET /api/stations/{slug}`: réponse existante inchangée, enrichie du champ additif `ski_areas`.
  Chaque domaine contient `stations`, sans la station consultée, uniquement actives et dédoublonnées.

Les objets publics omettent `source`, `verified_at` et `created_at`. Exemple fictif:

```json
{"ski_area":{"id":42,"name":"Grand Domaine Démo","slug":"grand-domaine-demo","status":"published",
"description":null,"cover_image_url":null,"piste_map_url":null,"altitude_min_m":0,
"altitude_max_m":null,"ski_area_km":null,"pistes_count":0,"green_pistes_count":null,
"blue_pistes_count":null,"red_pistes_count":null,"black_pistes_count":null,"lifts_count":null,
"forecast_open_date":null,"forecast_close_date":null,"season":null,
"updated_at":"2026-09-12T10:00:00+00:00","stations":[]}}
```

## Erreurs, déploiement et cache

Erreurs JSON: `400 invalid_json|unknown_fields|validation_error|invalid_pagination`, `401/403`
authentification, `404 ski_area_not_found|station_not_found|relation_not_found`, `409 slug_conflict|
relation_conflict|duplicate_station`; incident inattendu `500`. `fields` détaille les erreurs de champ.

Exécuter, après sauvegarde et **uniquement avec autorisation de production**:
`psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260912_add_ski_areas.sql`, puis redémarrer
l'API. Les écritures invalident les clés Redis domaines et les fiches station concernées; aucune action
front n'est requise. Un purge manuel global existant reste possible via `POST /api/admin/cache/purge`.

Limites: pas de suppression de domaine ni de hiérarchie dans cette première version; les URLs média
sont stockées selon la convention existante mais leur caractère HTTP(S) n'est pas revalidé par ces routes.
