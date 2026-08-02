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
| `ADMIN_ALLOWED_ORIGINS` | origines exactes séparées par des virgules, p. ex. `https://<front-production>` |
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
