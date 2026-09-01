# Cache Redis public

PostgreSQL reste l'unique source de vérité. Les réponses publiques éligibles
suivent un cache-aside Redis partagé entre workers; toute erreur Redis est
journalisée puis la vue PostgreSQL est exécutée normalement.

## Configuration

| Variable | Défaut | Rôle |
|---|---:|---|
| `REDIS_URL` | vide | URL Redis; vide désactive le cache |
| `PUBLIC_CACHE_ENABLED` | `true` | coupe globalement Redis |
| `PUBLIC_CACHE_DIRECTORY_TTL_SECONDS` | `86400` | annuaire stations |
| `PUBLIC_CACHE_STATION_TTL_SECONDS` | `86400` | fiche station |
| `PUBLIC_CACHE_WIDGETS_TTL_SECONDS` | `86400` | widgets |
| `PUBLIC_CACHE_SKI_PASSES_TTL_SECONDS` | `21600` | forfaits |
| `PUBLIC_CACHE_REGIONS_TTL_SECONDS` | `86400` | régions |
| `PUBLIC_CACHE_LOCK_TTL_SECONDS` | `10` | verrou anti-stampede |
| `PUBLIC_CACHE_LOCK_WAIT_SECONDS` | `0.2` | attente maximale d'un remplissage concurrent |
| `PUBLIC_CACHE_DEBUG_HEADERS` | `false` | ajoute `X-Cache` hors production |

Les connexions utilisent 150 ms de timeout de connexion et 200 ms de timeout
d'opération. Les clés sont `snow:public:resorts:list:<hash-paramètres>`,
`snow:public:station:<slug>`, `snow:public:widgets:<slug>`,
`snow:public:skipasses:<slug>[:<hash-paramètres>]`,
`snow:public:regions:list` et `snow:public:region:<slug>`. Les slugs sont
normalisés et validés; les paramètres sont sérialisés canoniquement puis hachés.

## Purges administrateur

Ces routes `POST`, couvertes par l'authentification et le CSRF admin globaux,
sont disponibles pour le futur frontend :

* `/api/admin/cache/stations/<slug>/purge`;
* `/api/admin/cache/resorts/purge`;
* `/api/admin/cache/public/purge`.

Les invalidations utilisent `SCAN`, jamais `KEYS`, et ont lieu après l'écriture
PostgreSQL. Une erreur d'invalidation est journalisée sans annuler l'écriture.
