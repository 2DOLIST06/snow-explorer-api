# Newsletter : inscription et préférences

## Déploiement

1. Dans DBeaver, sur la base PostgreSQL Snow Explorer, exécuter intégralement `migrations/20261031_newsletter_onboarding.sql`.
2. Déployer cette API.
3. Déployer le frontend associé `feat/newsletter-onboarding`.

La migration est additive et réexécutable. Elle ne réinitialise aucun abonnement. Les nouveaux thèmes distincts par station commencent désactivés. Aucune nouvelle variable d’environnement : les liens utilisent le service Brevo et la session membre existants.

## Parcours

- `POST /api/member/newsletter/start` reçoit `email` et `intent`. L’adresse normalisée retrouve le compte existant, ou crée un compte sans mot de passe. Pour une nouvelle adresse, un abonné `pending` est enregistré avec son consentement. Aucun réglage d’un abonné existant n’est modifié.
- L’email contient le lien de connexion membre habituel, valable 15 minutes et à usage unique. Le destinataire accède à ses choix dans son compte, puis les confirme.
- La réponse de `start` donne une capacité `draft_token`, valable au plus 15 minutes. Elle permet seulement `POST /api/member/newsletter/draft` pour remplacer la demande non confirmée. Elle ne donne accès ni à la session ni aux préférences existantes. La page peut ainsi proposer des choix facultatifs après la capture de l’email, sans renvoyer un nouveau message.
- Le lien récupère la dernière demande enregistrée, même ouvert sur un autre appareil. Une fois le lien consommé, la capacité d’édition anonyme est inutilisable.
- `GET /api/member/newsletter/request` permet au membre connecté de reprendre sa demande pendant 24 heures après sa création, y compris après rechargement ou connexion par mot de passe. `POST` sur la même route annule la demande.
- `POST /api/member/newsletter/apply` exige une session membre vérifiée, un jeton CSRF et une confirmation du choix. Il active les thèmes demandés pour les seules stations demandées, sans réinitialiser les autres thèmes ou stations. Une préférence articles générale n’est modifiée que si elle est présente explicitement dans la demande. Les statuts `bounced` et `complained` restent bloqués.
- L’ancienne route publique d’inscription conserve sa réponse générique, mais ne permet plus de réactiver une adresse existante ni de lui ajouter une station sans session membre vérifiée.

Chaque demande est limitée à 20 stations. Les noms de stations sont relus dans le catalogue actif. Les réglages par station exposent huit thèmes, une fréquence générale et une fréquence météo distincte. Les nouvelles stations ajoutées depuis l’éditeur commencent sans thème activé ; les abonnements existants sont conservés.

## Périmètre

Cette modification gère les inscriptions, les choix et leur persistance. Elle ne crée aucun moteur d’envoi de bulletins ou d’alertes automatiques. Les alertes neige à seuil déjà présentes dans l’API ne sont pas modifiées.

## Validation

- 42 tests ciblés comptes/newsletter passent.
- Suite complète avant le dernier test de protection de la route historique : 739 tests exécutés, 12 échecs et 36 erreurs ; les mêmes 48 cas sont présents sur `main` (723 tests), sans cas supplémentaire. Ces problèmes hors newsletter concernent notamment les fixtures de catalogue, les dépendances de tests et les tests de transport.
- 393 tests frontend passent et le build de production réussit dans le frontend associé.
- Contrôle visuel navigateur non réalisé : Chromium est bloqué par les restrictions de l’environnement.

## Vérification après déploiement

1. Déconnecté, saisir une nouvelle adresse dans la newsletter du footer et accepter le consentement : seuls email et consentement sont demandés au départ.
2. Ajouter des stations et des thèmes dans les choix facultatifs, enregistrer, puis ouvrir le lien reçu. Confirmer la sélection dans Mes abonnements.
3. Vérifier qu’aucun mot de passe n’est demandé pour utiliser ce lien et que les choix sont repris après rechargement du compte.
4. Depuis une station, choisir la météo : connecté, le compte doit s’ouvrir avec cette station et la météo présélectionnées ; déconnecté, l’email et la connexion doivent conserver cette intention.
5. Avec une adresse déjà abonnée à La Clusaz, ajouter la météo d’une autre station : la fréquence de La Clusaz et ses autres choix doivent rester identiques.
6. Vérifier la modification des fréquences, les thèmes forfaits/ouvertures/pistes, la désactivation des thèmes et le retrait d’une seule station.
