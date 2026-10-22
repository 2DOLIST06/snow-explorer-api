# Comptes membres Snow Explorer

Le menu public Compte utilise désormais des comptes membres indépendants. Les tables, cookies, jetons et routes des administrateurs restent distincts. Une même adresse peut posséder les deux comptes, sans transfert de privilèges ni de mot de passe. Aucun compte administrateur n’est créé par l’inscription publique.

## Parcours

- `/inscription` : adresse e-mail uniquement, puis lien de confirmation/connexion Brevo. Aucune session ni aucun accès aux préférences newsletter avant confirmation de l’adresse. Aucun mot de passe choisi par un tiers n’est préenregistré sur un compte non vérifié.
- `/connexion` : connexion par mot de passe, ou demande d’un lien par mail. Le mot de passe peut être défini dans le compte après la première connexion par mail.
- `/connexion/lien#jeton` : fragment retiré au chargement, puis consommation après un clic explicite. Jeton aléatoire de 256 bits, HMAC en base, 15 minutes, usage unique atomique.
- `/mon-compte` : deux onglets seulement, préférences newsletter et modification du mot de passe. Déconnexion vers `/`, jamais vers l’administration.

## Newsletter

Créer un compte n’abonne pas à la newsletter. Une session membre exige la vérification de l’adresse ; elle donne ensuite accès à l’abonnement portant cette même adresse. Les paramètres existants, stations suivies, alertes et jetons des anciens liens newsletter sont conservés. Le nouveau compte affiche pour le moment les thèmes, la fréquence et le désabonnement.

Un membre non abonné ou désabonné doit cocher explicitement le consentement pour s’inscrire/réactiver la newsletter. Les états bounced/complained ne sont pas réactivés. Les routes membres ne renvoient pas les jetons privés newsletter et ne permettent pas de choisir un autre propriétaire.

## API et sessions

Préfixe `/api/member` : register, login, login-link, login-link/consume, session, logout, password ; newsletter/access, newsletter/preferences (GET/POST), newsletter/subscribe et newsletter/unsubscribe.

Cookie HttpOnly `member_session`, durée identique au réglage de session existant ; paramètres Secure/SameSite existants. Origines autorisées et CORS avec credentials dédiés à `/api/member/*`. Écritures authentifiées protégées par un CSRF lié à la session. L’administrateur n’est jamais accepté comme membre par son seul cookie administrateur. Le changement de mot de passe (12 caractères minimum) révoque les anciennes sessions membres et liens en attente, puis garde le navigateur actuel connecté avec une nouvelle session.

Limitation des tentatives en base : 300 par IP et 10 par clé/IP sur 15 minutes ; une minute entre deux envois à un même membre. Les empreintes de clé de limitation sont des HMAC. Aucun secret ni adresse n’est inclus dans les diagnostics d’envoi.

## Déploiement

Déployer l’API avant le frontend. Les quatre nouvelles tables sont créées par l’initialisation `create_tables` existante. Pour une installation qui désactive cette initialisation, appliquer migrations/20261022_add_member_accounts.sql. Aucun changement destructif des données existantes.

Réutilise BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME, FRONTEND_URL et ADMIN_SESSION_SECRET. FRONTEND_URL doit être l’origine publique canonique, autorisée dans ADMIN_ALLOWED_ORIGINS. Les clés ne sont pas à recopier dans le dépôt.

Les tests automatisés simulent Brevo. Après déploiement, vérifier inscription, réception réelle, confirmation, définition du mot de passe, reconnexion, préférence newsletter et déconnexion sur le site public.

## Choix de newsletter par station

L’éditeur membre et celui des liens personnels par mail partagent les cinq thèmes par station : snowfall_enabled, snow_conditions_enabled, weather_enabled, resort_updates_enabled et articles_enabled. Chaque station possède sa newsletter_frequency (immediate/weekly/monthly). Les choix ouvertures/fermetures, remontées et forfaits sont retirés de l’interface ; les anciennes colonnes générales et weather_frequency sont conservées pour compatibilité.

**Appliquer migrations/20261022_newsletter_station_topics.sql avant de déployer cette version de l’API.** create_tables ne modifie pas une table déjà existante. La migration ajoute trois colonnes à newsletter_station_preferences (snowfall_enabled, articles_enabled, newsletter_frequency) et reprend les anciens choix généraux pour les valeurs manquantes. Elle est idempotente et ne remplace pas des choix par station déjà renseignés. Aucune migration de production n’est exécutée par la PR.

Les relations abonnés/stations existantes sont réutilisées, sans nouvelle table. L’ajout de station est idempotent ; l’enregistrement et le retrait restent limités au propriétaire vérifié. Le retrait d’une station enlève ses réglages et alertes associés pour cet abonné seulement, comme l’ancien centre de préférences.

Routes membres : GET/POST /api/member/newsletter/stations ; POST /api/member/newsletter/stations/{station_id} ; POST /api/member/newsletter/stations/{station_id}/remove. Les liens personnels utilisent leurs routes /api/newsletter/preferences/{token}/station-settings équivalentes.

Cette modification stocke les abonnements et préférences. Elle n’ajoute pas de moteur d’envoi automatique des bulletins ni de collecte d’informations en direct.
