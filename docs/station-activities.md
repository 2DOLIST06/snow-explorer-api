# Activités à faire sur les stations

Cette fonctionnalité utilise Flask/Peewee côté API et Next Pages/React côté site. Aucun dépôt Body Training Guide ni médiathèque n’était présent dans l’espace fourni ; le téléversement S3 existant, Tiptap et la navigation V3 sont réutilisés.

## Installation

Installer `requirements.txt`, puis appliquer `migrations/20261020_station_activities.sql` sur PostgreSQL avant de servir la nouvelle version. Le démarrage standard crée également les quatre tables manquantes avec Peewee. La migration est additive et réexécutable. Aucune migration distante ni aucun déploiement n’a été lancé pendant l’implémentation.

Les quatre tables sont `station_activities`, `activity_templates`, `activity_batches`, `activity_batch_results`. Le bloc est lié à l’identifiant stable de la station, pas à son slug. Aucun champ de `resort` ni de `station_widgets` n’est écrit par les opérations d’activités.

## Administration

L’édition d’une station comporte une section « Activités à faire » avec sa propre sauvegarde et un bouton de rechargement. Les nouveaux administrateurs utilisent les mêmes sessions et jetons CSRF que le reste de l’administration. Le rôle `admin` est exigé pour toutes les routes d’administration de cette fonctionnalité, y compris les intégrations. Aucun droit d’écriture OAuth Station Ops n’est ajouté.

`/admin/activites` permet de nommer, enregistrer, modifier et dupliquer des modèles. La sélection des stations se conserve entre les pages. Les filtres portent sur le nom/slug, le code pays, le département, l’identifiant de région, l’activation et la présence d’un bloc. Limite : 200 stations par lot, 50 éléments par bloc, 100 000 caractères par contenu, 1 Mo par bloc validé.

Un bloc déjà enregistré compte comme présent même s’il est vide ou désactivé. « Ajouter uniquement » est présélectionné. Le remplacement nécessite de sélectionner ce mode puis de confirmer le récapitulatif enregistré. Les copies deviennent indépendantes du modèle.

## API

- `GET /api/stations/:slug/activities` : bloc public, station active uniquement, sans cache.
- `GET|PUT /api/admin/stations/:slug/activities` : `{ block, revision }`, révision zéro pour l’absence de bloc ; conflit HTTP 409.
- `GET|POST /api/admin/activity-templates`, `PUT /api/admin/activity-templates/:id` : modèles et révisions.
- `GET /api/admin/activity-stations` : filtres, pagination par 30 stations et indication `has_block`.
- `POST /api/admin/activity-templates/:id/prepare` : `{ revision, station_ids, mode: "add"|"replace" }` ; produit l’identifiant du lot et le plan figé.
- `GET /api/admin/activity-batches/:id` : récapitulatif et résultats, accessibles à leur créateur.
- `POST /api/admin/activity-batches/:id/execute` : au plus 25 tentatives par requête. L’interface poursuit les stations en attente et propose la reprise des erreurs. Les conflits nécessitent un nouveau plan.

Le plan contient la copie du modèle et les révisions observées. Les écritures individuelles et de lot verrouillent la ligne station sur PostgreSQL, y compris quand le bloc n’existe pas encore. Chaque copie et son résultat sont validés dans la même transaction. Le verrou de lot sérialise les exécutions concurrentes d’un même lot. Les résultats réussis, ignorés ou en conflit ne sont pas rejoués. Les erreurs sont reprises après les stations encore en attente. Une réponse HTTP perdue peut donc être rejouée avec le même identifiant. Le lien `?lot=…` permet de retrouver le lot après rechargement.

## Rendu et widgets

Le bloc apparaît en bas de la fiche par défaut. L’onglet reprend le comportement V3 : la rubrique choisie remonte au début, avec défilement vers la navigation fixe. Le bloc et l’onglet sont absents quand le bloc est désactivé ou sans contenu actif affichable. Seules les stations sont concernées.

Les images utilisent le téléversement S3 existant ou une URL HTTPS saisie. Il n’existe pas de médiathèque à connecter dans le code fourni. Les liens conservent leurs paramètres, avec `rel="sponsored noopener noreferrer"`. Le contenu enrichi utilise Tiptap avec titres, texte, images et liens présentés éventuellement comme boutons ; il est nettoyé côté API avec Bleach et côté rendu avec sanitize-html. Les intégrations conservent leur code dans un champ distinct.

Les widgets sont exécutés dans des iframes `srcdoc` opaques (`allow-scripts allow-popups`, sans `allow-same-origin` sur le cadre extérieur), avec une CSP restrictive, sans navigation de la page parente ni formulaires. Le HTML de conteneur, les iframes HTTPS et les scripts externes HTTPS sont pris en charge. Les origines des scripts et iframes statiques sont ajoutées à la CSP ; les origines nécessaires aux API, ressources CSS, polices ou chargements dynamiques doivent être indiquées dans l’éditeur. Les liens HTML sont sécurisés, ainsi que les liens ajoutés au DOM du cadre par un script.

Sont incompatibles et rejetés à la sauvegarde : scripts inline, gestionnaires d’événements HTML, `srcdoc` partenaire, URL non HTTPS, formulaires, objets, balises non prises en charge. Un partenaire nécessitant le DOM parent, un `document.write` sur la page hôte, du stockage lié à l’origine hôte, une authentification tierce ou des permissions supplémentaires peut être incompatible. Les scripts externes qui exigent du code inline injecté ne peuvent pas contourner la CSP. Le fonctionnement de chaque partenaire réel doit être validé avec son code ; aucun compte d’affiliation réel n’a été utilisé.

Aucun gestionnaire général de consentement n’est installé dans le frontend fourni. Chaque widget demande donc un accord explicite avant tout montage, sur le site comme dans l’aperçu. Le bouton de retrait démonte le cadre ; l’accord n’est pas conservé entre les stations. Le changement de station et le démontage suppriment le document, ses scripts et les écouteurs de messages. Une erreur de script ou de ressource est signalée dans son cadre ; le reste de la fiche reste indépendant. Les messages de taille sont vérifiés par fenêtre source et jeton, puis bornés à 120–1600 pixels ; les grands widgets défilent dans leur cadre.

## Vérifications

Commandes de base :

```sh
python -m unittest tests.test_activities tests.test_admin_auth -v
python -m unittest tests.test_admin_station_patch.AdminStationPatchTests tests.test_database_lifecycle -v
```

Dans le dépôt frontend : `npm test`, `npx tsc --noEmit`, puis `npm run build` avec les variables d’API habituelles. `npm run lint` demande actuellement de créer une configuration ESLint ; aucune configuration générale n’a été ajoutée pour cette fonctionnalité.

Les tests d’activités utilisent une vraie base SQLite temporaire : formats mélangés et ordre, persistance, suivi des liens, sauvegarde individuelle concurrente, ajout par défaut, remplacement explicite, modèle figé, autres données préservées, échec après écriture avec rollback, reprise sans doublon, droits, propriété des lots et filtrage. Les tests ne simulent pas les verrous PostgreSQL ; le stress concurrent et l’exécution SQL de la migration sur PostgreSQL restent à vérifier dans un environnement disposant d’une base locale.

Une tentative de suite API globale a rencontré des erreurs de connexion au PostgreSQL local absent et des échecs des tests de conversion média existants, puis a été interrompue. Les tests ciblés ci-dessus sont indépendants de ces services. En particulier le test historique `AdminStationMapPersistenceTests` utilise encore le `db.atomic()` PostgreSQL global malgré sa base SQLite de fixture et échoue dans cet environnement.

Résultat de la livraison locale du 20 octobre 2026 : 51 fichiers de tests frontend réussis, TypeScript sans erreur, compilation Next.js de production réussie ; 27 tests activités/authentification et 20 tests de régression ciblés réussis. Les parcours Chromium ont vérifié la fiche sur ordinateur et à 390 px, l’onglet, le masquage, le consentement et son retrait, un script HTTPS de partenaire simulé exécuté une seule fois, l’interdiction d’accès au parent, une iframe HTTPS imbriquée, la sauvegarde individuelle et son conflit, la duplication des modèles, l’ajout en lot, le remplacement explicite, un conflit introduit entre préparation et exécution, le lien de reprise et le changement de station. Aucun `pageerror` n’a été relevé sur ces parcours. Les API météo et les images distantes du site étaient simulées ou indisponibles, sans bloquer ces vérifications.
