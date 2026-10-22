# Authentification administrateur

## Configuration Render

Appliquer d'abord `migrations/20260802_add_admin_authentication.sql`, puis définir sur le **service back-end** :

| Variable | Valeur / rôle |
|---|---|
| `ADMIN_SESSION_SECRET` | secret aléatoire privé d'au moins 32 caractères (64+ recommandés) |
| `ADMIN_SESSION_COOKIE_NAME` | `admin_session` |
| `ADMIN_SESSION_TTL_SECONDS` | `28800` (expiration absolue de 8 heures) |
| `ADMIN_SESSION_TOUCH_INTERVAL_SECONDS` | `300` (limite les écritures `last_seen_at`) |
| `ADMIN_COOKIE_SECURE` | `true` en production; `false` explicitement en HTTP local uniquement |
| `ADMIN_COOKIE_SAMESITE` | `None` pour le front de production cross-site (la chaîne littérale, avec `ADMIN_COOKIE_SECURE=true`) |
| `ADMIN_ALLOWED_ORIGINS` | origines exactes séparées par des virgules; par défaut `https://www.snow-explorer.com,https://snow-explorer.com` |
| `ADMIN_LOGIN_RATE_LIMIT` | `5` |
| `ADMIN_LOGIN_RATE_WINDOW_SECONDS` | `900` |
| `TRUST_PROXY_HEADERS` | `true` uniquement derrière un proxy de confiance qui remplace `X-Forwarded-For` |

Les échecs de connexion sont conservés dans PostgreSQL : la limite est donc partagée entre workers et instances. Une IP est aussi limitée à quatre fois la limite par fenêtre, afin d'éviter le contournement par rotation d'e-mails.

## Premier compte

Commande prioritaire (double saisie masquée, minimum 12 et maximum 1024 caractères) :

```bash
flask --app app.main create-admin --email admin@example.com
```

Sur une console sans entrée interactive, définir temporairement `ADMIN_BOOTSTRAP_EMAIL` et `ADMIN_BOOTSTRAP_PASSWORD`, exécuter `flask --app app.main bootstrap-admin`, puis **supprimer immédiatement ces deux variables**. Le bootstrap refuse de fonctionner dès qu'un administrateur existe.

## Contrat HTTP et front

* `POST /api/admin/auth/login` reçoit `email` et `password`, pose le cookie HttpOnly et renvoie l'utilisateur ainsi qu'un jeton CSRF, jamais le jeton de session.
* `GET /api/admin/auth/session` renvoie le même état utilisateur et le jeton CSRF, ou `401 {"authenticated": false}`.
* `POST /api/admin/auth/logout` et `POST /api/admin/auth/logout-all` exigent session et CSRF puis suppriment le cookie. La seconde révoque toutes les sessions courantes.
* Toute autre route `/api/admin/*` exige automatiquement une session active de rôle `admin`. Les écritures `POST`, `PUT`, `PATCH`, `DELETE` exigent `X-CSRF-Token`. `OPTIONS` reste public.

Le navigateur doit envoyer `credentials: "include"`, conserver le jeton CSRF uniquement en mémoire et l'ajouter aux écritures. Il ne doit jamais lire, demander ou transmettre `ADMIN_API_TOKEN`. Ce dernier reste uniquement dans le serveur Node historique pour ses éventuels consommateurs techniques et n'est pas accepté par l'authentification Flask des navigateurs.

Les cookies de production sont `HttpOnly`, `Secure`, `SameSite=None`, `Path=/`. Les mêmes attributs sont appliqués à leur création et à leur suppression. CORS n'autorise les credentials que pour les origines exactes de `ADMIN_ALLOWED_ORIGINS`; aucun couple wildcard + credentials n'est émis. Les routes publiques existantes `/api/*` restent accessibles sans session et sans credentials CORS.

Lors d'un changement de mot de passe futur, le service doit enregistrer `password_changed_at` et appeler `revoke_all_sessions(user.id)`. La vérification rejette déjà toute session antérieure à ce changement.

## Récupération par mail et Mon compte

Le formulaire `/admin/login` propose un lien de connexion envoyé via le service Brevo existant. Variables API : `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME`, `FRONTEND_URL=https://www.snow-explorer.com`. L’origine de FRONTEND_URL doit être dans ADMIN_ALLOWED_ORIGINS. Ne pas enregistrer les clés dans le dépôt.

Déployer l’API avant le frontend. La nouvelle table admin_login_links est créée par l’initialisation existante ; le SQL équivalent est fourni dans migrations/20261022_add_admin_login_links.sql pour les installations avec initialisation désactivée. Comptes et sessions existants sont conservés.

POST `/api/admin/auth/login-link` reçoit `{ "email": "..." }`. Messages explicites pour adresse inconnue (404), compte indisponible (403), configuration ou livraison en échec (503) et délai entre envois (429). Délai d’une minute par compte et limitation en base réutilisant le mécanisme existant. Demande et consommation exigent une origine autorisée.

Jeton aléatoire de 256 bits, HMAC stocké uniquement, valable 15 minutes, consommation atomique unique. Le lien `/admin/login-link#jeton` conserve le secret dans le fragment, retiré au chargement. Un clic explicite appelle POST `/api/admin/auth/login-link/consume` ; GET ne consomme jamais le lien.

La page `/admin/account` permet de changer le mot de passe sans l’ancien (12 caractères minimum). POST `/api/admin/auth/password` exige session et CSRF. Les anciennes sessions et les liens en attente sont invalidés ; une nouvelle session et un nouveau CSRF maintiennent le navigateur actuel connecté.

Vérifier réception et connexion après déploiement ; les tests automatisés simulent Brevo et n’envoient aucun mail réel.
