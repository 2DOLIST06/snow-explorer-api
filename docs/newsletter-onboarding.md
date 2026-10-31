# Newsletter : inscription immédiate et préférences

## Mise en place

Déployer l’API avant le frontend associé. Aucune migration SQL supplémentaire : les colonnes de la migration `20261031_newsletter_onboarding.sql` restent utilisées pour les accès au compte. Aucune nouvelle variable d’environnement.

## Inscription

`POST /api/member/newsletter/start` reçoit email et sélection. Le formulaire présente un bouton dédié à l’inscription et une mention sur les emails demandés et la désinscription. Le consentement est horodaté avec la version `v2-newsletter-button`.

La nouvelle inscription est active immédiatement. L’email de bienvenue contient le lien de gestion des préférences et le lien de désinscription ; son ouverture n’est pas nécessaire pour activer la newsletter. Un échec Brevo ne supprime pas l’inscription. Aucun compte ni session membre n’est créé par ce formulaire.

Si l’adresse normalisée est déjà active, la réponse est `already_subscribed` : aucun doublon, aucun email supplémentaire et aucune modification de ses réglages. Un ancien abonnement en attente peut être activé lors d’une nouvelle inscription explicite. Les adresses désabonnées peuvent se réinscrire explicitement ; les statuts bounced et complained restent bloqués.

Une capacité signée valable 15 minutes est fournie uniquement pour une nouvelle adresse. Elle permet de personnaliser immédiatement sa nouvelle inscription, sans passer par sa messagerie. Elle ne permet ni de se connecter ni de lire les préférences. Les nouvelles sélections remplacent les choix de cette inscription : retirer une station ou décocher un thème prend bien effet. Un désabonnement invalide cette possibilité d’édition.

## Accès aux abonnements existants

`POST /api/member/newsletter/login-link` est appelé uniquement lorsqu’une personne demande expressément à accéder à ses réglages ou choisit une connexion par email. Ce lien vérifie la possession de l’adresse pour accéder au compte ; il ne conditionne pas l’inscription à la newsletter.

Les modifications depuis le compte nécessitent toujours une session vérifiée et un jeton CSRF. L’ajout d’une station ou d’un thème conserve les autres abonnements. Les anciens liens de gestion reçus par email restent utilisables. Les anciens formulaires de brouillon restent compatibles pendant leur délai de validité.

## Présentation

La sélection et les abonnements enregistrés utilisent une liste compacte de stations avec un résumé des thèmes et fréquences. Un seul panneau de réglages est affiché à la fois, avec le nom de la station en titre. L’ajout est une recherche suivie d’un clic ; le sélecteur se ferme aussitôt. Les modifications en cours restent visibles et ne sont pas perdues quand on passe à une autre station.

Le bouton météo est placé au-dessus des informations météo, sur la page Météo, dans les fiches stations/domaines et sur les pages snowpark.

## Tests

- 43 tests ciblés API comptes/newsletter passent, dont cinq inscriptions successives de la même adresse sans nouvel email, l’activation sans validation, la personnalisation immédiate et l’isolation des comptes.
- Les envois automatiques de bulletins ne font pas partie de cette modification.

## Vérification après déploiement

1. Utiliser une adresse nouvelle : un seul champ email, inscription immédiatement active, mail de bienvenue sans validation.
2. Recommencer cinq fois avec cette adresse : message déjà inscrit, aucun nouveau mail ni doublon.
3. Ajouter deux stations : elles apparaissent en liste courte ; un seul panneau de choix reste ouvert et identifie la station concernée.
4. Retirer une station, enregistrer puis retrouver ses préférences avec le lien reçu : la station retirée doit être absente.
5. Sélectionner une station sur Météo ou ouvrir sa fiche : le bouton de réception météo doit être visible avant les prévisions.
6. Demander explicitement l’accès au compte : le lien de connexion protège les réglages existants, sans changer le statut déjà actif de la newsletter.
