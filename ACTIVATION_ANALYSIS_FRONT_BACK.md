# Analyse front/back – activation désactivation sur `admin/stations`

## Résumé exécutable

Le back **expose bien** `is_active` (snake_case), pas `isActive` (camelCase), sur les routes d’admin stations:

- `GET /api/admin/stations/` renvoie `items[].is_active`.
- `PATCH /api/admin/stations/<slug>` attend `{"is_active": <bool>}`.
- `PATCH /api/admin/stations/bulk-activation` attend `{"is_active": <bool>}`.

Si le front envoie/attend `isActive`, le back l’ignore ou rejette selon la route, et l’UI peut sembler “ne pas bouger”.

## Contrat API exact côté back

### 1) Liste admin stations
- Route: `GET /api/admin/stations/`
- Réponse: `{"items": [{ ..., "is_active": true|false }], "count": N}`
- Filtre optionnel: query param `active=true|false`.

### 2) Toggle unitaire
- Route: `PATCH /api/admin/stations/<slug>`
- Body accepté: `{"is_active": true|false}`
- Validation stricte: tout champ hors whitelist déclenche `400`.

### 3) Toggle en masse
- Route: `PATCH /api/admin/stations/bulk-activation`
- Body accepté: `{"is_active": true|false, ...}`

## Points de confusion possibles détectés

1. **Deux back-offices différents existent**:
   - `/api/admin/stations` (Blueprint `bp_admin_st`)
   - `/api/admin/resorts` (Blueprint `bp_admin`)

   Si le front lit sur l’un et écrit sur l’autre, le comportement observé peut paraître incohérent.

2. **Convention de nommage**:
   - Back: `is_active`
   - Si front: `isActive`
   => mismatch probable en lecture ET en écriture.

3. **Payload PATCH strict sur `/api/admin/stations/<slug>`**:
   - Un champ inconnu renvoie `400`.
   - Donc un payload `{ "isActive": false }` doit échouer.

## Checklist à envoyer au front

1. Vérifier dans Network que la page admin utilise **bien** `GET /api/admin/stations/`.
2. Vérifier que l’UI lit la clé `is_active` (et pas `isActive`).
3. Vérifier que le bouton envoie **exactement**:
   - `PATCH /api/admin/stations/<slug>` avec body JSON `{"is_active": false}` (ou true).
4. Vérifier le status code du PATCH:
   - `200` attendu
   - `400` => probablement clé invalide.
5. Après succès PATCH, re-fetch de la liste admin stations (ou update optimiste cohérent sur `is_active`).
6. Vérifier qu’aucun appel concurrent n’écrit ensuite l’état inverse (race côté front).

## Mini protocole de test front/back

### A. Lecture brute
- Appeler `GET /api/admin/stations/`
- Confirmer que `items[].is_active` reflète la DB.

### B. Ecriture brute
- Appeler `PATCH /api/admin/stations/<slug>` avec `{"is_active": false}`
- Refaire un `GET /api/admin/stations/`
- Confirmer le changement.

### C. Test anti-mismatch
- Appeler `PATCH /api/admin/stations/<slug>` avec `{"isActive": false}`
- Attendre `400` (normal, et utile pour confirmer le contrat).

## Côté back: conclusion d’analyse

Sur la route `admin/stations`, le contrat est cohérent en snake_case (`is_active`) en lecture/écriture.
Le problème le plus probable est un **mismatch de contrat côté front** (clé attendue/envoyée), ou un **mélange des endpoints** `/admin/stations` vs `/admin/resorts`.
