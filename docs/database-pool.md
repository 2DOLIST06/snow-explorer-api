# Pool de connexions PostgreSQL

Le backend utilise un `PooledPostgresqlDatabase` Peewee par processus. La
configuration par défaut est :

| Paramètre | Valeur | Variable Render |
| --- | ---: | --- |
| `max_connections` | 3 | `DB_POOL_MAX_CONNECTIONS` |
| `stale_timeout` | 300 secondes | `DB_POOL_STALE_TIMEOUT` |
| `timeout` | 5 secondes | `DB_POOL_TIMEOUT` |

Gunicorn exécute actuellement deux workers synchrones. Un worker ne traite
normalement qu'une requête à la fois ; la limite de 3 lui donne une petite
marge pour une commande ou une évolution threadée sans réserver les 20
connexions de la valeur par défaut Peewee. Le plafond théorique du service est
donc de **6 connexions physiques** (2 processus × 3), hors migrations, console,
autres services et connexions Render internes.

Une connexion rendue au pool est supprimée au prochain emprunt après cinq
minutes, ce qui limite l'exposition aux coupures réseau et aux connexions
serveur anciennes. Un emprunt attend au maximum cinq secondes lorsque le pool
est saturé : la requête échoue de manière bornée au lieu d'attendre sans fin.

La limite PostgreSQL propre au plan Render n'est documentée ni dans les fichiers
du projet ni dans une variable de configuration disponible ici. Il faut la
vérifier dans le tableau de bord Render avant d'augmenter la limite. Conserver
une marge pour les déploiements chevauchants (ancien et nouveau service peuvent
coexister), les tâches, migrations et consoles.

Les hooks Flask empruntent une connexion avant chaque requête et `db.close()` la
rend au pool pendant le teardown. Les commandes CLI utilisent explicitement un
`connection_context()`, car elles ne déclenchent pas ces hooks. Après
`create_tables()` au démarrage, les connexions inactives sont physiquement
fermées afin qu'aucun socket PostgreSQL ne puisse être hérité lors d'un fork
Gunicorn avec préchargement.
