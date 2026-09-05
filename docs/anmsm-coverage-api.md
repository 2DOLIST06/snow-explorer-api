# Contrat d'administration « Couverture ANMSM »

## Source de vérité et limites observées

`GET /api/admin/anmsm/coverage` est protégé par la session administrateur globale. Il
est strictement en lecture : il ne consulte ni Tourinsoft ni S3 et ne lance aucune
synchronisation, préparation ou publication.

La liste principale part de `resort`. Les associations validées viennent de
`station_external_mappings`; les originaux, aperçus, erreurs et validations viennent
des deux tables de candidats. Les URL actuellement affichées sont `resort.logo_url`
et `resort.pistes_large_map_url`. La nouvelle table
`anmsm_station_snapshots` ne duplique aucun statut calculé : elle conserve seulement
la dernière observation du catalogue amont (identité, disponibilité, URL et date),
information auparavant perdue après chaque requête réseau. Une synchronisation logo
complète et correctement parsée renseigne les colonnes logo. Le workspace administratif
des plans récupère déjà le flux complet `Donnees Stations` : après validation HTTP,
JSON et structurelle réussie, et avant de sérialiser ses lignes, il persiste les
observations plan de toutes les stations, y compris celles sans média. Cette écriture
de métadonnées ne télécharge, ne prépare et ne publie aucun média.

Conséquences importantes :

* sans observation persistée de la ressource, sa disponibilité vaut `unknown`, et
  jamais « absente » ;
* le flux ne fournit pas de slug ANMSM : `anmsm_station_slug` reste `null` ;
* aucune distinction fiable n'existe entre « station absente de Snow Explorer » et
  « rapprochement manuel en attente » : ces deux cas restent `anmsm_only` ;
* une suggestion nominale est informative et n'est jamais un mapping ;
* une URL publiée historique n'enregistre pas sa provenance. Elle reste exposée,
  mais `published_source` vaut `unknown`. La provenance `anmsm` n'est affirmée que
  pour un candidat `approved`, horodaté, dont la clé S3 correspond encore à l'URL
  publiée ;
* il n'existe pas de date globale historique de synchronisation avant cette
  migration. `last_anmsm_sync_at` est donc la date de dernière observation de la
  station, ou `null`.

Pour un plan, une observation négative ne devient `unavailable` que si
`piste_map_observation_complete=true`. Ce drapeau est posé exclusivement après que
`fetch_maps` a obtenu une réponse HTTP complète, décodé toute la liste JSON, confirmé
la présence de la collection `PLANPISTESs` dans le flux `Donnees Stations`, puis
parsé toutes les stations. Une erreur HTTP, JSON ou structurelle lève une exception
avant toute écriture et préserve le snapshot valide précédent. Une observation
positive reste exploitable même si elle provenait d'un import explicitement marqué
incomplet ; une observation négative incomplète reste `unknown`.

## Paramètres

`search`, `mapping_status`, `active`, `needs_station_contact`,
`needs_availability_control`, `missing_resource`, `resource`,
`availability_status`, `workflow_status`, `scope`, `sort`, `direction`, `page` et
`per_page` sont traités côté serveur. `resource` et `missing_resource` acceptent
`logo|piste_map`; `scope` accepte `all|snow_explorer|anmsm_only`; `sort` accepte
`name|coverage|missing_resources`. `per_page` est borné à 100 et les deux paginations
retournent toujours leur total exact. Le tri `coverage` ordonne : contact, contrôle,
disponible non importé, préparation, vérification, erreur, partiel, couvert.

`format=csv` exporte uniquement les stations du résultat filtré qui doivent être
contactées, sans adresse électronique. `format=json` est la valeur implicite.

## Statuts

`availability_status` vaut `available` uniquement après observation positive,
`unavailable` uniquement après observation complète négative, sinon `unknown`.

`workflow_status` est évalué dans cet ordre : `error` (erreur persistée), `published`
(preuve décrite ci-dessus), `to_prepare` (candidat pending sans aperçu),
`ready_to_review` (candidat pending avec aperçu), `available_not_imported`
(observation positive sans candidat), `missing_from_anmsm` (observation négative,
aucune URL publiée et aucun candidat exploitable), puis `unknown`.

Le candidat le plus récent décrit l'action courante et fournit les champs
`candidate_*`. La preuve de provenance publiée est recherchée séparément parmi tous
les candidats approuvés de la station : si la clé de l'un d'eux correspond encore à
l'URL actuellement publiée, `published_source=anmsm`, même lorsqu'un candidat plus
récent est `pending` ou en erreur. Cette règle est identique pour le logo et le plan.

Une ressource requiert un contact seulement avec une observation négative confirmée,
sans URL publiée et sans candidat exploitable. `missing_resource_types` contient les
codes concernés. `coverage_status` reprend la priorité de tri ;
`needs_availability_control` signale au contraire un manque non exploitable dont la
disponibilité reste inconnue.

## Réponse JSON

```json
{
  "ok": true,
  "snow_explorer_stations": [{
    "station_id": "val-disere", "station_name": "Val d'Isère",
    "station_slug": "val-disere", "station_is_active": true,
    "anmsm_external_station_id": "STAT123", "anmsm_station_name": "VAL D ISERE",
    "mapping_status": "matched", "mapping_validated": true,
    "last_anmsm_sync_at": "2026-09-05T10:00:00+00:00",
    "coverage_status": "ready_to_review", "needs_station_contact": false,
    "needs_availability_control": false, "missing_resource_types": [],
    "resources": {
      "logo": {
        "supported": true, "available_from_anmsm": true,
        "availability_status": "available", "workflow_status": "ready_to_review",
        "candidate_id": 42, "candidate_status": "pending",
        "current_published_url": null,
        "candidate_original_url": "https://anmsm.example/logo.png",
        "candidate_preview_url": "https://preview.example/logo.webp",
        "preparation_required": false, "error": null,
        "published_source": null, "needs_station_contact": false,
        "contact_reason": null
      },
      "piste_map": { "supported": true, "available_from_anmsm": null,
        "availability_status": "unknown", "workflow_status": "unknown",
        "candidate_id": null, "candidate_status": null,
        "current_published_url": null, "candidate_original_url": null,
        "candidate_preview_url": null, "preparation_required": false,
        "error": null, "published_source": null,
        "needs_station_contact": false, "contact_reason": null }
    }
  }],
  "anmsm_only_stations": [{
    "anmsm_external_station_id": "STAT999", "anmsm_station_name": "Station ANMSM",
    "anmsm_station_slug": null, "last_seen_at": "2026-09-05T10:00:00+00:00",
    "logo_available": true, "piste_map_available": null,
    "suggested_snow_explorer_station": null, "status": "anmsm_only"
  }],
  "pagination": {"page": 1, "per_page": 25, "total": 1, "pages": 1},
  "anmsm_only_pagination": {"page": 1, "per_page": 25, "total": 1, "pages": 1},
  "stats": {}
}
```

## Règles exactes des compteurs

Les statistiques sont globales (avant filtres) et dédupliquées par station. Les cinq
premières comptent respectivement toutes les lignes `resort`, les actives, les
mappings validés (y compris un éventuel conflit `mapping_error`), les `unmatched`,
puis les snapshots sans mapping validé.
`stations_needing_contact` et `stations_needing_availability_control` comptent les
booléens homonymes. Les compteurs `without_exploitable_*` exigent l'absence simultanée
d'URL publiée et d'aperçu candidat. Les dix compteurs de workflow comptent, pour la
ressource indiquée, l'égalité à `available_not_imported`, `to_prepare`,
`ready_to_review` ou `published`. `errors` additionne chaque ressource en `error` et
chaque station en `mapping_error`.

Les clés complètes sont : `snow_explorer_stations_total`,
`snow_explorer_stations_active`, `snow_explorer_stations_matched`,
`snow_explorer_stations_unmatched`, `anmsm_only_stations_total`,
`stations_needing_contact`, `stations_needing_availability_control`,
`stations_without_exploitable_logo`, `stations_without_exploitable_piste_map`,
`logos_available_not_imported`, `piste_maps_available_not_imported`,
`logos_to_prepare`, `piste_maps_to_prepare`, `logos_ready_to_review`,
`piste_maps_ready_to_review`, `logos_published_by_anmsm`,
`piste_maps_published_by_anmsm` et `errors`.

## Mise en service après déploiement

Dans un environnement autorisé, l'ordre opérationnel est :

1. appliquer `20260905_add_anmsm_station_snapshots.sql`, puis le complément
   idempotent `20260906_complete_anmsm_piste_map_observations.sql` si la première
   migration avait déjà été appliquée ;
2. lancer le workflow existant de synchronisation du catalogue stations/logos ;
3. ouvrir ou exécuter une récupération réussie du workspace administratif des plans,
   afin de persister le catalogue complet `Donnees Stations` ;
4. contrôler `logo_seen_at`, `piste_map_seen_at`,
   `piste_map_observation_complete` et `station_catalog_seen_at` ;
5. ouvrir ensuite la page de couverture, qui ne réalise elle-même aucun appel ANMSM.

Aucune de ces opérations de déploiement ou de synchronisation n'est exécutée par la
présente modification.
