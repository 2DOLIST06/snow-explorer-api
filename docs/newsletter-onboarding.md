# Newsletter : inscription immédiate et préférences

## Mise en place

Déployer l’API avant le frontend associé. Aucune migration SQL supplémentaire : les colonnes de la migration `20261031_newsletter_onboarding.sql` restent utilisées pour les accès au compte. Aucune nouvelle variable d’environnement.

## Inscription

`POST /api/member/newsletter/start` reçoit email et sélection. Le formulaire présente un bouton dédié à l’inscription et une mention sur les emails demandés et la désinscription. Le consentement est horodaté avec la version `v2-newsletter-button`.

La nouvelle inscription est active immédiatement. L’email de bienvenue contient un bouton « Accéder à mon compte » utilisant le même lien de connexion à usage unique que les autres accès au compte, ainsi que le lien de désinscription ; son ouverture n’est pas nécessaire pour activer la newsletter. Un échec Brevo ne supprime pas l’inscription. Le compte membre est créé ou retrouvé dès l’inscription avec la même adresse normalisée, sans mot de passe imposé et sans ouvrir de session. La possession de l’adresse n’est vérifiée qu’à l’ouverture du lien de connexion ou par le mot de passe existant.

Si l’adresse normalisée est déjà active, la réponse est `already_subscribed` : aucun doublon, aucun email supplémentaire et aucune modification de ses réglages. Un ancien abonnement en attente peut être activé lors d’une nouvelle inscription explicite. Les adresses désabonnées peuvent se réinscrire explicitement ; les statuts bounced et complained restent bloqués.

Une capacité signée valable 15 minutes est fournie uniquement pour une nouvelle adresse. Elle permet de personnaliser immédiatement sa nouvelle inscription, sans passer par sa messagerie. Elle ne permet ni de se connecter ni de lire les préférences. Les nouvelles sélections remplacent les choix de cette inscription : retirer une station ou décocher un thème prend bien effet. Un désabonnement invalide cette possibilité d’édition.

## Accès aux abonnements existants

`POST /api/member/newsletter/login-link` est appelé uniquement lorsqu’une personne demande expressément à accéder à ses réglages ou choisit une connexion par email. Ce lien vérifie la possession de l’adresse pour accéder au compte ; il ne conditionne pas l’inscription à la newsletter.

Les modifications depuis le compte nécessitent toujours une session vérifiée et un jeton CSRF. L’ajout d’une station ou d’un thème conserve les autres abonnements. Les anciens liens de gestion reçus par email proposent d’envoyer un lien de connexion via POST /api/member/newsletter/account-link. Ils ne sont jamais convertis directement en session complète. La désinscription reste accessible sans connexion. Les anciens formulaires de brouillon restent compatibles pendant leur délai de validité.

## Présentation

La sélection et les abonnements enregistrés utilisent une liste compacte de stations avec un résumé des thèmes et fréquences. Un seul panneau de réglages est affiché à la fois, avec le nom de la station en titre. Un seul composant de préférences est utilisé après inscription et dans le compte, quel que soit le statut antérieur. « Ajouter une station » ouvre immédiatement la liste des stations avec des cases à cocher et une recherche facultative. Plusieurs stations peuvent être cochées puis ajoutées ensemble ; le sélecteur se ferme après confirmation. Les articles et guides, leurs fréquences et les thèmes de chaque station se règlent dans le même formulaire. Les modifications en cours restent visibles et ne sont pas perdues quand on passe à une autre station.

Le bouton météo est placé au-dessus des informations météo, sur la page Météo, dans les fiches stations/domaines et sur les pages snowpark.

## Tests

- 51 tests ciblés API comptes/newsletter passent, dont cinq inscriptions successives de la même adresse sans nouvel email, l’activation sans validation, la personnalisation immédiate et l’isolation des comptes.
- Les envois automatiques de bulletins ne font pas partie de cette modification.

## Vérification après déploiement

1. Utiliser une adresse nouvelle : un seul champ email, inscription immédiatement active, mail de bienvenue sans validation.
2. Recommencer cinq fois avec cette adresse : message déjà inscrit, aucun nouveau mail ni doublon.
3. Ajouter deux stations : elles apparaissent en liste courte ; un seul panneau de choix reste ouvert et identifie la station concernée.
4. Retirer une station, enregistrer puis retrouver ses préférences avec le lien reçu : la station retirée doit être absente.
5. Sélectionner une station sur Météo ou ouvrir sa fiche : le bouton de réception météo doit être visible avant les prévisions.
6. Demander explicitement l’accès au compte : le lien de connexion protège les réglages existants, sans changer le statut déjà actif de la newsletter.


## Cohérence du compte et renvoi des liens

Le compte et les abonnements sont uniques par adresse email normalisée. Après une connexion par lien, le membre peut définir un mot de passe dans Mon compte. Les connexions ultérieures par lien ou mot de passe retrouvent le même identifiant de compte et les mêmes préférences. Les inscriptions répétées ne remplacent jamais le mot de passe existant.

Le lien du mail de bienvenue est valable 15 minutes pour se connecter. Il ne conditionne pas l’activation de la newsletter. Le bouton « Recevoir un nouveau lien » appelle POST /api/member/login-link/resend sur la même page et n’affiche une réussite qu’après l’acceptation de l’envoi par le service email. Le délai minimal d’une minute entre les envois et les limites de tentatives restent appliqués.

Les nouveaux fragments contiennent le secret de connexion et une signature séparée autorisant uniquement le renvoi d’un email au propriétaire du compte pendant 90 jours. Cette signature ne peut jamais ouvrir une session. Un ancien lien expiré encore présent en base reste reconnu ; pour un lien très ancien ou non identifiable, la page demande l’adresse email puis envoie le lien sur place. Le fragment est effacé de la barre d’adresse dès l’arrivée sur la page.

Le bouton « Inscrire une autre adresse email » remet le formulaire newsletter à zéro sans désabonner la première adresse ni déconnecter le compte ouvert. L’adresse utilisée est affichée dans le résultat. Une connexion avec une autre adresse peut être demandée explicitement ; le compte courant n’est remplacé qu’après une connexion réussie.

Validation du correctif : 51 tests ciblés API passent, dont le parcours newsletter → lien de bienvenue → définition du mot de passe → connexion avec ce mot de passe, les liens expirés ou remplacés, le renvoi réel, les échecs de livraison et la protection des anciens liens newsletter.


## Interface unique et sauvegarde complète

`GET /api/member/newsletter/settings` fournit les réglages complets du compte connecté sans créer d’abonnement. `POST` sur la même route enregistre la sélection complète dans une transaction : stations, thèmes cochés ou décochés, fréquences générales et par station. L’adresse est toujours celle de la session ; le client ne peut pas choisir un autre propriétaire. Le formulaire de personnalisation immédiate après inscription utilise le même format et la même sauvegarde complète via sa capacité limitée existante.

Les anciens composants de réglages généraux et de stations ont été supprimés du frontend. La sélection multiple, le panneau unique de station et le bouton « Enregistrer mes choix » proviennent du même composant dans tous les parcours. Une station sans thème sélectionné peut être conservée. Une station devenue inactive peut être conservée par son abonné actuel, mais ne peut pas être ajoutée par un autre compte. Les liens depuis les pages stations complètent les choix déjà enregistrés sans les remplacer.

Validation : 56 tests ciblés API et 403 tests frontend passent. Les tests de composants comparent les contrôles réellement affichés après inscription et pour les comptes sans abonnement, actifs ou désabonnés ; ils vérifient aussi la liste immédiatement visible, la sélection multiple et la sauvegarde complète. Aucun changement de schéma ni envoi automatique de bulletin.


## Ajout direct depuis une station

`POST /api/member/newsletter/follow` nécessite une session et un jeton CSRF. Il accepte uniquement `station_id`, `topic` et éventuellement `language`. Il ajoute le thème demandé au compte connecté sans remplacer les autres abonnements ni les fréquences déjà choisies. Pour la météo en pause, un clic explicite réactive la fréquence du vendredi. Un ajout déjà actif renvoie `already_followed: true`. Les adresses bounced/complained restent bloquées et la limite de vingt stations s’applique aux nouveaux ajouts. Aucun email de connexion ni bulletin n’est envoyé par cette route.

Le frontend doit déployer ce nouveau parcours après cette API. Les visiteurs non connectés ouvrent la page de compte avec le champ email et le choix station/thème préparé. Une adresse existante garde ce choix pendant la connexion, puis le retrouve dans l’éditeur habituel. Le message de bienvenue ne comporte plus de phrase expliquant l’absence de confirmation.

Validation : 60 tests ciblés comptes/newsletter passent, dont l’ajout direct, les clics répétés, la conservation des fréquences, la météo en pause, la réactivation, les permissions/CSRF et la limite de stations. Aucune migration SQL supplémentaire.


## Choix météo et enneigement commun

Le clic explicite sur le thème weather via `/newsletter/follow` active ensemble weather, snowfall et snow_conditions. Il conserve les fréquences déjà choisies et les autres thèmes. `already_followed` ne vaut true que si le groupe complet est déjà actif. Les formats et colonnes historiques restent inchangés, sans migration SQL. Les autres thèmes restent indépendants.

Le désabonnement depuis le compte reste protégé par session et CSRF, préserve les préférences et ne touche pas les autres comptes. Validation : 61 tests ciblés comptes/newsletter passent, dont le groupe météo/neige, la répétition du clic et le désabonnement global authentifié.


## Liens de retour aux fiches

Les préférences de station incluent `station_slug`, calculé depuis la station canonique ; la valeur fournie par le client n’est jamais utilisée comme destination. Le champ vaut null pour une station inactive. Le format est accepté lors de la sauvegarde complète. Les nouvelles inscriptions reçoivent également leur intent normalisé pour afficher le lien dès leur arrivée sur le compte. Aucune migration SQL.
