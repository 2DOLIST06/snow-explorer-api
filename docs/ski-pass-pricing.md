# Grilles tarifaires des forfaits

Les anciennes grilles libres de `station_widgets.config.forfaits` restent
disponibles sans modification. Elles ne sont pas migrées automatiquement :
elles ne contiennent généralement ni saison, ni période datée, ni devise et
leurs prix sont des textes libres. Déduire ces informations inventerait des
données.

## JSON d'import définitif

Le document est un objet contenant `station_slug`, `season`, `currency`
(facultatif, `EUR` par défaut), `source_url` (facultatif), `updated_at`
(informatif et ignoré), `periods` et `passes`. Chaque période contient `id`,
`name`, `start_date`, `end_date`. Chaque forfait contient `id`, `name`,
`duration_days` (entier positif ou null), `duration_label` et `prices`.
Chaque prix contient `period_id`, `category`, `category_label`, `price_type`,
puis soit `price` (`fixed`), soit `price_min`, `price_max` et éventuellement
`dynamic_label` (`dynamic`). Les tableaux déterminent l'ordre, exposé également
comme `sort_order` par l'API publique.

## Endpoints

* `GET /api/forfaits/stations/{slug}?season=2026-2027` : grille publique ; sans
  paramètre, la saison au libellé le plus récent est renvoyée.
* `GET /api/admin/ski-passes/stations/{slug}?season=...` : une grille ou toutes
  les saisons de la station.
* `GET /api/admin/stations/{slug}/ski-passes` : toutes les saisons dans une
  enveloppe `seasons`, avec les identifiants de base nécessaires à l'éditeur.
* `POST /api/admin/ski-passes/import/preview` : validation sans écriture.
* `POST /api/admin/stations/{slug}/forfaits/preview` : même validation via
  l'URL utilisée par l'éditeur de station; le slug de l'URL est injecté dans le
  document avant validation.
* `POST /api/admin/ski-passes/import` : remplacement transactionnel.
* `POST /api/admin/stations/{slug}/forfaits/import` : même remplacement via
  l'URL utilisée par l'éditeur de station.
* `PUT /api/admin/stations/{slug}/ski-passes/{season_id}` : sauvegarde
  transactionnelle de toute la grille éditée. L'admin recharge ensuite le GET.
* `DELETE /api/admin/ski-passes/stations/{slug}/seasons/{season}` : suppression.

Toutes les routes `/api/admin/*` utilisent la protection centralisée de session
administrateur et le jeton CSRF existants.
