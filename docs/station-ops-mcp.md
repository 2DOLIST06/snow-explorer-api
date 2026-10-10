# Snow Explorer Content Ops — MCP privé 1.0 / OAuth 2.1

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

## OAuth 2.1 pour ChatGPT Web

Le backend est à la fois resource server MCP et authorization server OAuth.
Aucun fournisseur externe et aucun compte/mot de passe OAuth séparé.
L'authentification `/api/admin/*` garde ses cookies et son CSRF ; les tokens MCP
ne la remplacent pas. OAuth est actif par défaut et n'a pas de flag d'activation.

| Contrat | Valeur par défaut exacte |
| --- | --- |
| MCP / resource RFC 8707 | `https://snow-explorer-api-3.onrender.com/mcp/station-ops` |
| Issuer RFC 8414/9207 | `https://snow-explorer-api-3.onrender.com` (sans slash final) |
| Protected resource metadata RFC 9728 | `https://snow-explorer-api-3.onrender.com/.well-known/oauth-protected-resource/mcp/station-ops` |
| Authorization server metadata | `https://snow-explorer-api-3.onrender.com/.well-known/oauth-authorization-server` |
| Authorization | `https://snow-explorer-api-3.onrender.com/oauth/station-ops/authorize` |
| Token | `https://snow-explorer-api-3.onrender.com/oauth/station-ops/token` |
| Révocation RFC 7009 | `https://snow-explorer-api-3.onrender.com/oauth/station-ops/revoke` |
| Client CIMD | `https://chatgpt.com/oauth/client.json` |
| Redirect | `https://chatgpt.com/connector_platform_oauth_redirect` |

### CIMD, SDK et contrat OpenAI vérifié

Contrat vérifié le 10 octobre 2026 dans la documentation officielle
[OpenAI Authentication](https://developers.openai.com/plugins/build/auth) et le
[document CIMD stable](https://chatgpt.com/oauth/client.json).
Le callback et le client stables nécessitent RFC 9207 et le même issuer exact dans
les deux metadata. Le CIMD actuel propose `none` et `private_key_jwt` dans le champ
pluriel, même si sa préférence singulière est `private_key_jwt`. Ce serveur propose
uniquement `none` : l'intersection autorise le client public avec PKCE.

Pas de DCR ni de registre de clients. Le serveur récupère le CIMD HTTPS à chaque
nouvelle autorisation, exige le client_id exact, une redirect présente dans le CIMD
ET dans la liste locale, et les méthodes/grants compatibles. Fetch limité à 64 KiB,
timeouts connect/read, sans suivi de redirection, origine strictement chatgpt.com,
port standard, sans credentials/query/fragment. Toute indisponibilité refuse le flow.
La configuration n'accepte pas d'origine CIMD alternative ni de wildcard.

Le SDK officiel `mcp==1.30.0` conserve le transport, le backend Bearer, AccessToken,
RequireAuthMiddleware, AuthContextMiddleware et le chemin RFC 9728. Les handlers AS
de cette version n'implémentent pas le contrat CIMD pluriel/RFC 9207 demandé ;
authorize/token sont donc des endpoints OAuth standard Flask qui utilisent la même
base et les services admin existants. Ce choix ne change pas le protocole MCP et ne
nécessite pas DCR. Le JSON metadata de ressource utilise l'issuer chaîne exact :
AnyHttpUrl du SDK ajouterait un slash à un issuer origine et casserait RFC 9207.

### Login, consentement et flux

1. ChatGPT découvre metadata et utilise le client CIMD public.
2. GET authorize : `response_type=code`, client_id et redirect_uri exacts,
   `resource` exact obligatoire, scopes, `code_challenge` et méthode `S256` obligatoires.
3. Une transaction browser persistée (10 minutes) redirige vers un handle aléatoire
   hashé. Le navigateur reçoit un cookie `__Host-`, Secure/HttpOnly/SameSite=Lax/Path=/.
4. Une session admin existante valide est réutilisée sans touch. Sinon une page login
   minimale backend réutilise `authenticate_admin_credentials`, Argon2, le rate limit
   et `create_admin_session`. Le frontend Next.js est séparé et aucun contrat de retour
   OAuth de son login n'est présent dans ce dépôt : pas de redirection arbitraire vers lui.
5. Consentement explicite affichant séparément Lecture et Écriture demandées. Les POST
   login/consent exigent un CSRF HMAC lié au cookie browser et au handle de flow.
   Ce mécanisme est indépendant du CSRF admin frontend et du token endpoint OAuth.
6. Consentement accepté : code à usage unique de 5 minutes lié à l'admin, au client,
   à la redirect, à resource, aux scopes et à PKCE S256. Consentement refusé : access_denied.
   State est conservé ; chaque callback succès/erreur contient l'issuer exact dans `iss`.
   Client/redirect non fiables ne déclenchent aucun callback ; l'erreur locale contient `iss`.
7. POST token en form-urlencoded : resource, client_id, redirect_uri et code_verifier
   sont vérifiés ; consommation et création des tokens sont atomiques.
8. Bearer opaque envoyé dans Authorization seulement. Chaque requête vérifie token,
   expiry, révocation, grant, resource et admin encore actif/admin. Un changement de
   mot de passe invalide également les grants antérieurs.

Les scopes autorisés sont `station-ops:read` et `station-ops:write`. Read est obligatoire ;
write seul est refusé. Aucun scope non demandé n'est accordé. Les six tools de lecture,
y compris apply_dry_run, exigent read. apply_commit exige read + write ; il conserve
absolument toutes les protections APPLY. Chaque tool expose `securitySchemes` oauth2,
également dans `_meta.securitySchemes`, avec ses scopes ; les annotations restent
readOnly/destructive. Un refus de scope d'outil porte un challenge
`_meta["mcp/www_authenticate"]` pour permettre une nouvelle autorisation.

Access token : au plus 1 heure, plafonnée à l'expiration du grant. Refresh token :
30 jours absolus à compter du grant, sans prolongation à la rotation. Chaque refresh
consomme immédiatement l'ancien et retourne une nouvelle paire. Les refresh consommés
restent conservés tant que le grant est valide : leur réutilisation révoque toute la
famille, y compris les nouveaux access tokens. PostgreSQL utilise un verrou de grant
pour sérialiser refresh/révocation. La révocation d'un access ou refresh révoque le grant.
POST revoke accepte client_id et token, sans secret client ; token inconnu retourne 200.
Les scopes ne peuvent pas être changés par refresh : refaire authorization/consent.

Tous les codes, access tokens, refresh tokens et handles sont générés via secrets et
stockés uniquement en SHA-256. Aucun mot de passe ni token brut OAuth n'est persisté.
Aucune nouvelle clé cryptographique serveur n'est nécessaire. Le secret admin existant
reste utilisé uniquement par le service de session admin, pas comme clé OAuth.

### Migration et stockage

Migration à exécuter **plus tard**, séparément, après validation :
`migrations/20261010_add_station_ops_oauth.sql`.

Tables OAuth dédiées : station_ops_oauth_flows, station_ops_oauth_codes,
station_ops_oauth_grants, station_ops_oauth_tokens, station_ops_oauth_rate_buckets.
Aucune création automatique de ces tables au démarrage. Aucune migration de production
n'a été exécutée dans cette tâche. Aucun trigger ni donnée station ne change.
Un accès avant migration échoue fermé ; les 401 MCP donnent les metadata, sans détail DB.

Rate limit partagé en base, par endpoint/IP : 60 requêtes/minute, incluant token,
authorize et revoke. Login OAuth : 20 tentatives/15 minutes en plus du rate limit admin
existant (5 échecs par paire IP/email, 20 par IP sur 15 minutes par défaut).
Compteurs OAuth atomiques, indépendants des workers. Sans confiance proxy configurée,
la limite porte sur l'IP du proxy ; configurer TRUST_PROXY_HEADERS seulement avec un
proxy de confiance. Les fenêtres sont fixes, donc un burst à la frontière reste possible.

Cleanup opportuniste : au maximum 100 rows par table/request OAuth ; requêtes sur expiry
indexées, pas de DELETE massif. Tokens expirés/révoqués ou liés à un grant révoqué purgés ; grants expirés/révoqués
supprimés seulement sans tokens enfants.
Les tombstones de refresh consommés restent jusqu'au terme absolu pour détecter le replay.
Sans trafic OAuth, la purge attend la prochaine requête ; pas de cron requis.

### Variables et compatibilité legacy

| Variable | Défaut / effet |
| --- | --- |
| STATION_OPS_OAUTH_ISSUER | Origine HTTPS Render ci-dessus, sans slash/path/query/fragment ; facultative |
| STATION_OPS_OAUTH_CLIENT_IDS | Liste CSV exacte du client CIMD stable ; facultative |
| STATION_OPS_OAUTH_REDIRECT_URIS | Liste CSV exacte du callback stable ; facultative |
| STATION_OPS_MCP_LEGACY_TOKEN_ENABLED | absent/false : Bearer statique refusé ; true/1/yes/on : compatibilité temporaire |
| STATION_OPS_MCP_TOKEN | uniquement si legacy activé ; ancienne valeur compromise à supprimer/rotater |
| STATION_OPS_MCP_ALLOWED_HOSTS | Liste DNS-rebinding existante, indépendante des clients OAuth |

Les lifetimes, limits et resource sont des constantes, sans variables supplémentaires.
ADMIN_SESSION_SECRET, ADMIN_SESSION_COOKIE_NAME, ADMIN_COOKIE_SAMESITE, TTL et rate limit
admin restent ceux du frontend ; cookies posés par le login OAuth toujours Secure.
STATION_OPS_APPLY_COMMIT_ENABLED n'est pas modifié ; conserver false en production.
Si le mode de callback indiqué par ChatGPT diffère, copier les deux valeurs exactes du
management dans les listes et vérifier le CIMD ; aucun localhost/wildcard OAuth par défaut.

Le legacy ne doit pas être utilisé pour ChatGPT. Il exige à la fois flag explicite et
STATION_OPS_MCP_TOKEN non vide et dispose temporairement des scopes read + write ;
il ne contourne jamais APPLY. Supprimer/rotater l'ancienne valeur exposée avant utilisation.

### Transport et confidentialité

HTTPS obligatoire hors fixtures TESTING. Le SDK vérifie DNS rebinding. Le middleware
refuse Authorization dupliqué, tokens query-only, paramètres MCP en query et corps
MCP >16 MiB. Les 401 portent WWW-Authenticate Bearer avec resource_metadata et read.
Pas de cookie admin comme substitut au token MCP. Les erreurs de scope global donnent
403 ; les erreurs de tool restent des CallToolResult structurés.
Responses OAuth et MCP : no-store. Pages OAuth : CSP anti-frame, no-referrer, nosniff.
Formulaires OAuth limités à 16 KiB, paramètres dupliqués refusés. Token endpoint sans
CSRF frontend, sans client_secret ni assertion. Errors standard sans SQL/stacktrace.
Ne pas activer les logs DEBUG de headers/bodies ni journaliser les Location OAuth.
Les logs applicatifs contiennent seulement événements et identités/scopes autorisés ;
les arguments Station Ops et credentials ne sont pas journalisés.

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
| station_catalog | Filtres country_code, region_id, department, is_active, ski_area_id facultatifs ; limit et offset facultatifs | Page compacte, total, returned, next_offset, has_more | REPEATABLE READ / READ ONLY |
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

### Catalogue compact pour les gros audits

`station_catalog` permet de commencer un audit RESEARCH d'un pays ou d'une région
sans transporter le snapshot complet. Le contrat de `station_scan` est inchangé.
Le nouvel outil déclare `readOnlyHint=true` et le scope OAuth `station-ops:read`
dans `securitySchemes` et `_meta.securitySchemes`.

Les filtres géographiques conservent les types SCAN : chaînes uniquement,
notamment `is_active="true"`/`"false"` et `ski_area_id="123"`.
`limit` est un entier de 1 à 250, avec défaut 100 ; `offset` est un entier de
0 à 9223372036854775807, avec défaut 0. Les arguments inconnus et valeurs
invalides sont refusés avec une erreur d'outil structurée de statut 400.

Premier appel, puis page suivante avec les mêmes filtres :

```json
{"country_code": "FR", "limit": 100, "offset": 0}
```

La réponse contient `schema_version="1.0"`, `catalog_version="1.0"`,
`generated_at`, `scope.filters`, `order_by="id"`, `limit`, `offset`, `total`,
`returned`, `has_more`, `next_offset`, `stations` et `schema_findings`.
`total` compte toutes les stations correspondant aux filtres ; `returned` compte
la page. Utiliser `next_offset` pour poursuivre jusqu'à `has_more=false`, auquel
cas `next_offset=null`. Une page vide ou un offset au-delà du catalogue est valide.
L'ordre par `id` est stable. Chaque page possède sa propre transaction : des
créations ou suppressions entre appels peuvent décaler les offsets. La pagination
ne remplace pas la revalidation des données par REVIEW et APPLY.

Chaque station contient exclusivement :

- `id`, `name`, `slug`, `is_active`, `country_code` ;
- `region_id`, `region_name` (libellé stocké sur la station), `department` ;
- `latitude`, `longitude`, `altitude_min_m`, `altitude_max_m`, `ski_area_km` ;
- `website_url`, `updated_at` ;
- `has_cover_image`, `has_logo`, `has_piste_map`.

Les indicateurs de médias sont calculés en SQL sur la présence d'une URL non vide.
Ils ne vérifient ni l'accessibilité des URLs ni les collections de maps. Le dernier
indicateur examine `pistes_small_map_url` et `pistes_large_map_url`. Aucune URL de
média, texte éditorial, piste, remontée, forfait, widget ou collection n'est chargé
dans la page. Aucun accès web n'est effectué. Les champs d'identité disponibles
restent fidèles aux données stockées, sans troncature silencieuse.

Le service utilise l'inventaire du schéma physique existant. Un champ optionnel
absent est retourné à `null` avec un diagnostic `schema_findings`. Pour un média,
`null` signifie une présence inconnue si une colonne nécessaire manque et qu'aucune
colonne disponible ne démontre sa présence. Un filtre dont la colonne manque
provoque `station_ops_schema_incompatible` (statut 503), sans élargir le périmètre.

PostgreSQL : `REPEATABLE READ / READ ONLY`, un inventaire du schéma puis deux
requêtes SELECT (comptage et page), indépendamment du nombre de stations.
Le filtre domaine utilise une sous-requête sur la relation plusieurs-à-plusieurs,
sans N+1. SQLite conserve la protection `query_only` des tests. Aucune donnée
métier ou session admin n'est écrite. Aucun changement de schéma n'est effectué.

Les tests couvrent 73 stations françaises, les pages de 100 et 250 stations, et
une réponse MCP complète de moins de 160 KiB pour 73 stations malgré des contenus
et URLs de médias volumineux en base. Cette mesure inclut la représentation texte
et `structuredContent`. La limite de page borne le nombre de stations, sans
garantir un plafond universel en octets pour des champs d'identité anormalement longs.

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
L'audit MCP ajoute l'admin_id OAuth vérifié ; l'acteur du moteur APPLY reste inchangé.
En mode legacy, aucun admin individuel n'est attribué.

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

1. station_catalog avec le scope pays/région souhaité, en suivant next_offset pour les gros audits ; station_scan reste disponible pour le snapshot détaillé d'une station via id/slug.
2. ChatGPT fait la recherche externe avec ses propres capacités.
3. research_contract puis research_validate pour chaque lot RESEARCH 1.0.
4. compare avec compare_payload.
5. review sans décisions pour afficher les opérations pending et ambiguïtés.
6. L'utilisateur examine et décide explicitement par operation_id.
7. review avec ces décisions pour récupérer le plan et son fingerprint courants.
8. apply_dry_run avec les mêmes candidats, décisions et fingerprint.
9. Éventuellement apply_commit, après décision explicite et activation serveur séparée.

Toute modification stale impose de refaire REVIEW et les décisions concernées.
Le fingerprint n'est pas une signature de sécurité. Le scope OAuth write n'est pas une approbation des opérations ; le plugin doit contrôler
ses demandes d'écriture et présenter les décisions à l'utilisateur.

Le serveur implémente OAuth Authorization Code + PKCE S256/CIMD et est prévu pour
ChatGPT Web → Plugins → Add custom MCP server → OAuth. Le raccordement réel nécessite
migration et déploiement séparés. Aucun raccordement ChatGPT/Render live n'est revendiqué.

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

Les résultats du travail OAuth et les commandes exactes sont consignés dans le
rapport `docs/station-ops-oauth-report.txt`. Les tests OAuth simulent le CIMD stable
vérifié, exécutent le login/consentement/token endpoint et le client MCP officiel sur
ASGI en mémoire et SQLite isolé. Ils n'utilisent ni API Render ni PostgreSQL de production.
Le test admin CORS a reçu un mock DB afin de ne plus demander un PostgreSQL local.
Pas de test TLS/proxy Render, de concurrence PostgreSQL live ou de raccordement ChatGPT
réel dans cette tâche ; ces vérifications sont à faire en staging avant déploiement.
