# Snow Explorer Content Ops — MCP privé 1.0

La façade MCP appelle directement les services Python Station Ops validés.
Elle n'effectue aucune recherche web et ne possède aucun moteur d'écriture propre.
Le serveur est nommé **Snow Explorer Content Ops**. Le plugin ChatGPT privé et
ses capacités de recherche externe seront configurés séparément.

## URL et runtime

URL prévue après publication et déploiement distincts :

`https://snow-explorer-api-3.onrender.com/mcp/station-ops`

Ce travail ne publie ni ne déploie le serveur. Le transport est **Streamable HTTP**
du SDK Python officiel `mcp==1.30.0`, avec réponses JSON et mode stateless.
Le SDK gère JSON-RPC, initialize, notifications, versions et découverte tools/list ;
aucun protocole maison n'est ajouté. Il n'existe ni session MCP persistante,
reprise SSE, ressource publique, health check MCP, outil shell, SQL ou fichier.
GET/DELETE suivent le comportement du SDK en mode stateless, après authentification.

L'entrée ASGI `app.mcp_main:app` sert MCP et adapte l'application Flask existante
avec `a2wsgi`. Le Dockerfile conserve Gunicorn et sa configuration de timeout,
avec `uvicorn_worker.UvicornWorker`. Les routes Flask, leur authentification et
leur cycle de connexion restent ceux de `app.main:app`.

Dépendances directes ajoutées :

- `mcp==1.30.0` : SDK officiel et ses dépendances transitives (Starlette, AnyIO,
  Pydantic, HTTPX, JSON Schema, composants de transport/auth du SDK).
- `uvicorn==0.38.0` et `uvicorn-worker==0.4.0` : worker ASGI sous Gunicorn.
- `a2wsgi==1.10.10` : adaptateur WSGI léger, pour conserver Flask.

Si Render utilise une commande native plutôt que le Dockerfile, sa commande
future devra cibler l'entrée commune, par exemple :

```sh
gunicorn -c gunicorn.conf.py -k uvicorn_worker.UvicornWorker -b 0.0.0.0:5001 app.mcp_main:app
```

La commande existante `app.main:app` continue à servir Flask seul et n'expose pas
MCP. Aucune commande de service Render n'est modifiée dans cette tâche.

## Authentification machine et HTTPS

Configurer côté serveur **STATION_OPS_MCP_TOKEN** et envoyer uniquement :

```http
Authorization: Bearer <PRIVATE_MACHINE_TOKEN>
Accept: application/json, text/event-stream
Content-Type: application/json
```

Le placeholder n'est pas un vrai token. Utiliser un secret aléatoire suffisamment
long, enregistré dans le gestionnaire de secrets du connecteur et de Render.
Le serveur lit la variable à chaque requête et compare les octets avec
`hmac.compare_digest`. Une rotation ne nécessite pas de modifier le code.
Il ne crée aucune session admin et n'accepte ni cookie admin ni CSRF comme
substitut au Bearer. L'authentification admin existante reste inchangée ; un
Bearer MCP ne permet pas d'accéder aux endpoints `/api/admin`.

| Cas | HTTP | Code |
| --- | --- | --- |
| Variable absente, vide ou uniquement espaces | 503 | station_ops_mcp_unavailable |
| Authorization absent, incorrect ou dupliqué | 401 | unauthorized |
| Token seulement en query string | 401 | unauthorized |
| Bearer valide avec query string | 400 | mcp_query_not_allowed |
| HTTP hors fixtures TESTING | 403 | https_required |
| Chemin MCP inconnu après authentification | 404 | not_found |

L'authentification protège aussi GET, DELETE, OPTIONS et le namespace `/mcp` avant
le SDK et avant toute connexion métier. Les refus ne retournent ni token ni
valeur de variable ; les 401 portent `WWW-Authenticate: Bearer`.
Les réponses MCP portent `Cache-Control: no-store`.

**HTTPS est obligatoire hors tests.** La façade vérifie le schéma ASGI, pas un
X-Forwarded-Proto arbitraire. Derrière Render, Uvicorn doit reconnaître uniquement
le proxy TLS de confiance : régler `FORWARDED_ALLOW_IPS` sur les IP du proxy si
nécessaire. Ne choisir `*` que si toute connexion à l'origine provient d'un proxy
qui écrase les headers forwarded des clients et dont la frontière est vérifiée.
Un simple header client ne suffit pas à convertir HTTP en HTTPS. La terminaison
TLS et cette configuration de proxy n'ont pas été vérifiées ou modifiées en production.

La protection DNS rebinding du SDK reste active. Les hosts par défaut sont
`snow-explorer-api-3.onrender.com`, `localhost`, `localhost:*`, `127.0.0.1`,
`127.0.0.1:*`. Les Origin HTTPS correspondants sont autorisés lorsqu'un Origin est
fourni. Pour un domaine personnalisé, configurer la liste séparée par virgules
`STATION_OPS_MCP_ALLOWED_HOSTS` avec les hosts réellement utilisés ; ne pas la
remplacer par un wildcard global. Aucun CORS anonyme supplémentaire n'est ouvert.

## Outils et contrats

Les schémas exacts sont exposés par **tools/list**, construits dans
`app/mcp/tools.py`. Les annotations MCP distinguent les outils readOnly des
écritures. Seul apply_commit porte readOnlyHint=false et destructiveHint=true.
Ces annotations assistent le client ; les protections serveur restent obligatoires.

Les paramètres sont l'objet **arguments** de tools/call, sans enveloppe payload.
Les réponses Station Ops sont conservées dans **structuredContent**, également
représentées en JSON dans content pour les clients MCP. Une réussite porte
isError=false ; un refus d'outil porte isError=true. Le schéma de sortie commun
est un objet JSON : le contrat détaillé reste celui du service correspondant,
documenté dans [station-ops-api.md](station-ops-api.md).

| Outil | Arguments exacts | Sortie | Données métier |
| --- | --- | --- | --- |
| station_scan | Aucun obligatoire ; uniquement id, slug, country_code, region_id, department, is_active, ski_area_id | Snapshot SCAN inchangé | REPEATABLE READ / READ ONLY |
| research_contract | Objet vide uniquement | research_version, field_statuses, source_types, research_levels, candidate_fields, limits, json_schema | Aucune requête métier |
| research_validate | Objet RESEARCH 1.0 complet, validé par le validateur canonique | valid, errors, warnings, summary, results/audit, excluded_candidates, compare_payload | Aucune requête métier |
| compare | candidates obligatoire uniquement | Contrat COMPARE inchangé, avec results/changes/review_items | REPEATABLE READ / READ ONLY |
| review | candidates obligatoire ; decisions facultatif | Contrat REVIEW inchangé, results/operations et apply_plan/plan_fingerprint | REPEATABLE READ / READ ONLY |
| apply_dry_run | candidates, decisions, plan_fingerprint obligatoires uniquement | Contrat APPLY en mode dry_run, execution_id et opérations ready | REPEATABLE READ / READ ONLY |
| apply_commit | candidates, decisions, plan_fingerprint, confirm_apply obligatoires uniquement | Contrat APPLY en mode commit, execution_id et opérations applied | Transaction SERIALIZABLE existante |

SCAN conserve ses types d'entrée actuels : toutes les valeurs de filtre sont
**des chaînes**, y compris is_active="true"/"false" et ski_area_id="123".
Les autres outils utilisent exactement les candidats et décisions JSON Station Ops.
Les tableaux candidates exigent au moins un objet. Les structures internes sont
validées par les services existants, sans deuxième moteur métier.
Le schéma discovery de research_validate accepte un objet pour laisser le
validateur canonique retourner ses diagnostics complets, notamment sur les champs
inconnus et données manquantes. research_contract fournit son schéma détaillé
canonique, dérivé du générateur RESEARCH, sans copie manuelle.

Exemples d'arguments :

```json
{"country_code": "FR"}
```

Pour compare, puis review sans décision :

```json
{
  "candidates": [
    {"client_ref": "external-001", "data": {"id": "a", "altitude_max_m": 2600}, "field_sources": {}}
  ]
}
```

Pour les deux outils APPLY :

```json
{
  "candidates": [
    {"client_ref": "external-001", "data": {"id": "a", "altitude_max_m": 2600}, "field_sources": {}}
  ],
  "decisions": [
    {"client_ref": "external-001", "operations": {"<operation_id FROM REVIEW>": "approved"}}
  ],
  "plan_fingerprint": "<SHA-256 FROM REVIEW>"
}
```

apply_commit exige en plus `"confirm_apply": true`. Les placeholders doivent être
remplacés par les IDs et le fingerprint renvoyés par REVIEW ; rien n'est inventé.
Une enveloppe MCP standard pour un outil de lecture ressemble à :

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {"name": "station_scan", "arguments": {"country_code": "FR"}}
}
```

Le client MCP officiel doit initialiser le serveur et négocier la version avant
ses appels ; le SDK prend en charge ce handshake.

## Séparation des approbations et écritures

apply_dry_run **fixe mode="dry_run" côté serveur**. Toute clé mode, confirm_apply,
operations, apply_plan ou approve_all sur cet outil est refusée avant l'appel APPLY.
Il n'effectue aucune écriture métier ni touch de session admin.

apply_commit **fixe mode="commit" côté serveur**, sans accepter un mode client.
Il exige au moins une décision approved visant un operation_id ; le moteur REVIEW
revérifie ensuite les candidats et décisions exactes. Une décision forged/inconnue,
obsolète ou portant sur un candidat absent n'est jamais acceptée. Aucune transition
pending→approved n'est automatique et aucune approbation globale n'est ajoutée.
Les opérations sensibles requièrent leur approbation explicite existante.

Le wrapper ne fournit aucun raccourci vers execute_plan ou des UPDATE libres.
Il appelle **apply_candidates** et conserve intégralement :

- STATION_OPS_APPLY_COMMIT_ENABLED, désactivé par défaut ; seules true/1/yes/on activent.
- confirm_apply=true littéral JSON.
- Recalcul COMPARE et REVIEW, fingerprint et préconditions anti-stale.
- Limite de 1000 opérations approuvées pour commit.
- Transaction SERIALIZABLE, locks, whitelist et contraintes physiques.
- Rollback atomique, relecture stricte post-write et audit APPLY.

Le secret MCP ne peut pas activer le kill switch. Le refus commit désactivé conserve
le code **station_ops_apply_commit_disabled** et le status logique **403**.
L'absence de décision approved est refusée explicitement avec
**explicit_review_approval_required** ; pas de commit automatique d'un plan vide.

L'acteur d'audit APPLY est la valeur serveur fixe **station_ops_mcp**, dans le champ
admin_id du logger existant. Aucun compte admin n'est usurpé et aucun ID d'acteur
client n'est accepté. L'audit MCP distingue également le nom de l'outil.
Un seul secret machine ne permet pas d'attribuer individuellement les actions à
plusieurs utilisateurs ; des identités/scopes distincts constituent une évolution future.

Le wrapper n'ouvre aucune transaction métier autour des services. Il exécute les
fonctions synchrones dans un worker dédié avec contexte Flask, puis ferme la
connexion acquise par ce worker. Un appel MCP métier à la fois et deux workers
WSGI sont prévus pour respecter le pool Peewee de trois connexions par défaut.
Ce choix limite la concurrence ; il ne crée pas de N+1 ou de boucle par candidat.
Les batches restent traités en un appel au service existant.

## Erreurs, limites et journalisation

Une erreur métier d'outil est une réponse MCP normale (HTTP 200), avec isError=true
et **status logique** conservé dans structuredContent, par exemple :

```json
{
  "code": "station_ops_apply_commit_disabled",
  "message": "Station Ops APPLY commit is disabled on this environment.",
  "status": 403,
  "execution_id": "<SERVER EXECUTION UUID>"
}
```

Les détails existants sont conservés lorsqu'ils sont disponibles : issues,
operation_id, field, execution_id, schema_findings et fingerprints. Les erreurs
REVIEW de décision portent status=409. Les problèmes de schéma portent status=503.
RESEARCH invalide conserve ses diagnostics et ajoute code=invalid_research_payload,
status=400, isError=true. Un résultat COMPARE contenant des candidats invalides
reste un diagnostic COMPARE normal ; il n'est pas transformé en erreur de transport.

Les erreurs de transport/auth restent de vrais HTTP 400/401/403/413/503. Un corps
MCP trop grand donne HTTP 413 ; un lot de 1001 candidats donne une erreur d'outil
status=413, jamais un 500 générique. JSON malformé, clés dupliquées et nombres
non finis sont refusés avant parsing protocolaire par le SDK. Le SDK conserve ses
propres erreurs standard JSON-RPC pour les méthodes/versions/requêtes invalides.

Limites partagées avec Station Ops : **1000 candidats**, **16 MiB pour le corps MCP
complet**, et **1000 opérations approuvées par APPLY commit**. Le pays doit être
orchestré en plusieurs lots. Les champs mode et opérations arbitraires sont toujours
refusés. SCAN conserve son snapshot complet actuel ; aucune pagination ou réduction
silencieuse de son contrat n'est introduite. De gros snapshots restent à organiser
par filtres côté orchestrateur et selon les budgets de contexte du client.

Le logger **station_ops.mcp.audit**, niveau INFO, écrit un événement JSON par appel :
nom d'outil, timestamp UTC, success/refused/error, nombre de candidats et execution_id
APPLY si présent. Les refus de transport utilisent tool="transport" et un code fixe.
Un nom d'outil inconnu est journalisé comme "unknown", sans entrée libre du client.
Aucun token, cookie, CSRF, argument candidat, contenu massif, SQL ou exception brute
n'est journalisé par la façade. Les erreurs inattendues retournent uniquement
station_ops_mcp_failed, message générique et status=500, sans stacktrace.
Ne configurer aucun logging DEBUG des headers/secrets dans l'infrastructure externe.

## Workflow conseillé et limites d'intégration ChatGPT

1. station_scan avec le scope pays/région/station souhaité.
2. ChatGPT fait la recherche externe avec ses propres capacités.
3. research_contract puis research_validate pour chaque lot RESEARCH 1.0.
4. compare avec compare_payload.
5. review sans décisions pour afficher les opérations pending et ambiguïtés.
6. L'utilisateur examine et décide explicitement par operation_id.
7. review avec ces décisions pour récupérer le plan et son fingerprint courants.
8. apply_dry_run avec les mêmes candidats, décisions et fingerprint.
9. Éventuellement apply_commit, après décision explicite et activation serveur séparée.

Toute modification stale impose de refaire REVIEW et les décisions concernées.
Le fingerprint n'est pas une signature de sécurité. Le Bearer est un secret machine
partagé, pas une preuve d'approbation utilisateur ; le futur plugin doit contrôler
ses demandes d'écriture et présenter les décisions à l'utilisateur.

Le serveur utilise le protocole MCP standard et est testé avec le client officiel.
Il n'implémente **pas OAuth** : utiliser un connecteur/plugin permettant de fournir
le Bearer statique dans Authorization. Une interface ChatGPT n'acceptant que OAuth
ne pourra pas être raccordée directement sans étape d'authentification dédiée.
Aucun plugin n'est installé ou publié et aucun raccordement ChatGPT/Render réel
n'est revendiqué dans cette tâche.

## Vérifications locales

Les tests MCP couvrent auth, variable absente, token/query/logs, HTTPS, DNS rebinding,
initialize/tools/list/tools/call, client MCP officiel, erreurs structurées et 413,
services réels en SQLite isolé, absence d'écriture en lecture/dry_run, kill switch,
confirmation, fingerprint, approbations, commit isolé par le moteur existant,
cleanup de connexion et compatibilité des routes admin cookie/CSRF.

Le harnais de transport fonctionne entièrement en mémoire ; dans le sandbox de
travail, une permission réseau est nécessaire aux sockets internes de réveil entre
threads du client ASGI. Les fixtures métier interdisent explicitement toute connexion
PostgreSQL de production. Les tests PostgreSQL existants couvrent l'isolation,
verrous et rollback par simulation ; aucune nouvelle transaction PostgreSQL live
ou activation du kill switch de production n'est exécutée.

Résultat local final : **272 tests Station Ops réussis**, dont **35 tests MCP**,
et **111 sous-tests réussis**. Les suites supplémentaires de cycle de connexion
et cache public passent (**20 tests**), ainsi que l'authentification admin sous
harnais SQLite isolé (**13 tests**). `pip check`, syntaxe Python et whitespace
passent. Un avertissement de dépréciation Starlette concerne uniquement l'usage
HTTPX de TestClient ; le test avec le client MCP officiel passe.
Aucun build Docker, test TLS/proxy Render ou raccordement ChatGPT live n'est revendiqué.
