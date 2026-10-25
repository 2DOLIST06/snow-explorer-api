---
name: snow-explorer-orchestrator
description: Auditer, rechercher, comparer, revoir et préparer les créations ou mises à jour des stations de ski dans Snow Explorer. Utiliser dès que l'utilisateur demande un audit de station, région ou pays, des données manquantes ou obsolètes, des créations de stations, ou une maintenance du catalogue Snow Explorer.
---

# Snow Explorer Content Ops

## Mission

Être le point d'entrée unique pour la maintenance factuelle du catalogue Snow Explorer.

Pipeline obligatoire :

RESEARCH externe -> `research_validate` -> `compare` -> `review` -> `apply_dry_run` -> éventuellement `apply_commit`.

Utiliser `station_scan` pour lire l'état réel du catalogue avant de conclure qu'une station existe, manque ou doit être modifiée. Utiliser `research_contract` au début d'un nouveau workflow important ou dès que le contrat RESEARCH paraît incertain : le backend est la source canonique du schéma.

## Recherche web

La recherche est faite par ChatGPT avec ses capacités web, jamais par le backend Snow Explorer.

Appliquer la politique de sources décrite dans [Politique RESEARCH](references/research-policy.md).

Ne jamais inventer une donnée. Quand une information n'est pas trouvée, la marquer `not_found`; quand elle n'a pas été recherchée, `not_researched`; en cas d’écart, choisir d’abord la valeur la plus pertinente selon la politique de sources ; réserver `conflicting` aux cas réellement impossibles à départager; quand l'identité est douteuse, `ambiguous`.

Une donnée `not_found`, `not_researched`, `conflicting` ou `ambiguous` ne doit pas être placée dans `data` comme proposition. RESEARCH ne génère jamais de `clear_fields` automatiquement.

## Complétude obligatoire et recherches officielles

Une station n’est pas terminée tant que toutes les informations du périmètre demandé ne sont pas renseignées et vérifiées. Une validation technique du JSON ne prouve pas sa complétude. Pour un lot de création à importer, exclure entièrement toute station incomplète : ne jamais livrer un candidat partiel dans le JSON importable, même sous le nom de brouillon. Les audits sans import peuvent conserver les constats de recherche.

Avant rédaction et avant livraison, contrôler un inventaire par champ : identité et localisation, site, description, altitudes minimale et maximale, kilomètres du domaine propre à la station, total et couleurs des pistes, total et types des remontées, nombre de snowparks, dates d’ouverture et de fermeture, forfaits de la saison (catégories, durées, périodes, prix et conditions), appartenance aux domaines existants et les trois médias. Un champ non applicable doit être justifié ; une absence d’information ne signifie ni zéro ni non applicable.

Rechercher systématiquement chaque information dans les sources officielles, même si une source secondaire fournit déjà une valeur. Consulter les pages de la station et de l’exploitant, les rubriques domaine, plans, tarifs et calendrier, les documents liés et la billetterie officielle. Pour les lacunes, poursuivre auprès des offices de tourisme, de la commune, du département ou de l’autorité compétente, avec des recherches ciblées par champ. Lire les contenus trouvés, pas seulement leurs extraits de moteur. Ne pas s’arrêter à une page d’accueil, une annonce ou une erreur de lien ; chercher la page actuelle et les documents officiels associés. Conserver les URLs effectivement consultées et ce qu’elles établissent. Ne pas déclarer une recherche exhaustive si l’accès a été bloqué.

Distinguer le domaine physique de la station, le secteur prévu pour une saison et les pistes ouvertes le jour de consultation. Une ouverture partielle ne justifie pas d’omettre les caractéristiques permanentes vérifiées. Renseigner les champs selon le périmètre du contrat, documenter ce périmètre dans research et expliquer clairement la restriction saisonnière dans la description. Ne jamais mélanger kilomètres du domaine entier et compteurs du seul secteur ouvert dans un même ensemble de statistiques ; si le contrat ne permet pas une représentation fidèle, conserver les valeurs sourcées dans research et signaler cette limite. Ne pas réutiliser un tarif ou une date ancienne comme valeur confirmée de la saison demandée.

Si une information requise manque après ces recherches, exclure la station entière du lot importable ainsi que ses médias et ses entrées research associées ; ne pas conserver une fiche partielle et ne pas inventer de valeur. Dans la réponse finale, nommer chaque station exclue et lister tous ses champs manquants avec leurs motifs (non publié, non trouvé après recherche, conflit non résolu ou accès bloqué). Conserver les notes hors du lot importable pour reprendre ultérieurement. Aucun champ facultatif sans objet, tel qu’un rattachement à un domaine inexistant pour une station indépendante, ne doit être fabriqué ; vérifier et documenter cette absence. Un zéro vérifié est une donnée, une absence de preuve n’est jamais zéro. Distinguer not_researched de not_found et poursuivre les recherches officielles avant de décider l’exclusion. S’il ne reste aucune station complète, ne fournir aucun JSON à importer et expliquer les exclusions. Les arbitrages factuels ordinaires restent résolus en autonomie.

## Audit d'une station

1. Lire l'état actuel avec `station_scan` par `id` ou `slug` lorsque disponible.
2. Obtenir le contrat courant via `research_contract` si nécessaire.
3. Rechercher les champs demandés, en priorité dans les sources officielles.
4. Construire un candidat RESEARCH 1.0 avec une provenance par champ.
5. Appeler `research_validate` et corriger toute erreur de contrat.
6. Envoyer le `compare_payload` validé à `compare`.
7. Présenter les changements utiles ; résoudre les écarts de données en autonomie, sans demander un arbitrage champ par champ.
8. Appeler `review` sans décisions pour obtenir les `operation_id` et le plan proposé.
9. Ne jamais approuver à la place de l'utilisateur.
10. Après approbation explicite, appeler `review` avec les décisions exactes.
11. Exécuter obligatoirement `apply_dry_run` avant tout commit.
12. N'appeler `apply_commit` qu'après confirmation explicite de l'utilisateur et seulement avec le fingerprint issu du plan revu. Respecter le kill switch du backend.

## Audit d'un pays ou d'une région

Pour une demande telle que « audite toutes les stations françaises » :

1. Appeler `station_scan` avec le filtre géographique demandé pour obtenir le catalogue Snow Explorer réel.
2. Rechercher aussi des listes externes suffisamment larges pour détecter des stations absentes du catalogue. Ne pas considérer la base actuelle comme une liste exhaustive du pays.
3. Réconcilier les identités de manière conservatrice. Une homonymie ou une correspondance incertaine reste ambiguë.
4. Travailler par lots raisonnables. Respecter les limites backend de 1000 candidats et 16 MiB, mais préférer des lots plus petits quand la recherche web ou la revue humaine devient difficile à contrôler.
5. Pour chaque lot, produire RESEARCH -> validate -> COMPARE.
6. Consolider un audit lisible : stations inchangées, corrections proposées, nouvelles stations potentielles, conflits, données non trouvées et éléments bloqués.
7. Ne passer à REVIEW/APPLY que pour les candidats réellement retenus.

## Nouvelles stations

Pour une station absente :

- utiliser `identity.kind = "new"` selon le contrat courant ;
- rechercher tous les champs du périmètre demandé selon la règle de complétude ci-dessous ;
- ne jamais inventer un ID interne ;
- ne jamais créer automatiquement un domaine skiable ambigu ;
- laisser REVIEW bloquer une création insuffisante plutôt que compléter artificiellement les données.

## Décisions et écritures

`review` sans `decisions` sert à présenter les opérations. L'absence de décision signifie `pending`.

Une phrase explicite de l'utilisateur telle que « approuve toutes les modifications non sensibles de ce lot » peut être traduite en décisions `approved` pour les opérations concernées. Les opérations sensibles (`clear`, retrait de relation, autre action destructive) doivent être signalées séparément et ne doivent être approuvées que si l'utilisateur les autorise explicitement.

Ne jamais construire ni injecter un `apply_plan` arbitraire : toujours utiliser les candidats originaux, les décisions exactes et le `plan_fingerprint` recalculé par REVIEW.

`apply_dry_run` est obligatoire avant `apply_commit`.

Si `apply_commit` est refusé parce que le kill switch serveur est désactivé, l'expliquer et ne pas chercher à contourner la protection.

## Présentation à l'utilisateur

Pour les gros audits, présenter d'abord une synthèse exploitable plutôt qu'un JSON massif : nombres de stations analysées, inchangées, à modifier, nouvelles, ambiguës, invalides, champs en conflit et sources problématiques. Fournir le détail par station lorsque nécessaire.

Conserver les URLs de sources et leur provenance dans les objets RESEARCH, mais éviter de noyer la réponse finale sous toutes les URLs si l'utilisateur ne les demande pas.

## Interdictions

Ne jamais :

- supposer l'état actuel du catalogue sans `station_scan` lorsqu'il est nécessaire à la décision ;
- transformer `not_found` en suppression ;
- générer automatiquement `clear_fields` ;
- choisir arbitrairement entre des sources contradictoires ;
- approuver automatiquement une opération REVIEW ;
- contourner le kill switch APPLY ;
- envoyer des opérations arbitraires directement à APPLY ;
- prétendre qu'une écriture a réussi sans le retour `applied` d'APPLY.

## Créations par lots avec médias

Pour une demande de JSON à importer, suivre [le workflow par lots](references/station-batches.md). Ce flux prépare un fichier et ne lance aucun APPLY. Le pipeline RESEARCH/COMPARE/REVIEW/APPLY reste requis pour les écritures directes via Station Ops.


## Descriptions et périmètre de création

Pour toute création ou réécriture de description, lire et appliquer impérativement [les règles de description](references/station-descriptions.md) : consulter les pages sources et réunir les faits avant de rédiger une vraie présentation développée, utile pour comprendre le lieu, son domaine et un séjour. Commencer par le domaine skiable et donner le poids principal au ski, sans imposer une première phrase ou un plan de paragraphes. Les informations utiles au séjour viennent ensuite. Inclure les événements et faits marquants vérifiés liés au ski, à la glisse, à l’hiver ou à la station lorsqu’ils sont significatifs, à une place secondaire et jamais en ouverture. L’histoire générale sans ce lien reste très secondaire et facultative, sans développement sur les musées ou la vie ancienne. Traiter chaque station indépendamment : recherche ouverte sur ses particularités durables, notes et sélection propres au lieu, puis rédaction sans reprendre la trame ou les sujets de la fiche précédente. Ne pas transformer les champs du JSON en plan éditorial. Construire un ordre cohérent à partir de ces faits, sans plan ni phrases préfabriqués ; écrire comme une personne qui explique simplement le lieu à une autre, avec des mots courants et des formulations directes, sans récit imagé, poésie ni prose publicitaire. Une fiche trop courte ou une juxtaposition de faits doit être réécrite avant livraison. Avant livraison, effectuer la comparaison du brouillon avec les formulations et l’ordre des sources réellement utilisées, corriger les proximités, puis relire les points qui isolent artificiellement une idée. Comparer les fiches entre elles seulement à la fin pour repérer un recyclage des sujets et de l’organisation ; en cas de trame répétée, reprendre la recherche et la construction du texte concerné. Ces contrôles doivent être réalisés sur le texte final ; ne pas seulement affirmer qu’il a été rédigé de zéro ou que la mémoire a été effacée. Les altitudes utiles sont permises, les compteurs de pistes et remontées restent dans les champs structurés. Le périmètre exact des champs figure dans [le workflow par lots](references/station-batches.md). Ne pas enrichir automatiquement avec d’autres blocs.

## Médias de création

Appliquer obligatoirement [cover, écusson et plan](references/station-media.md) avant de produire un lot : cover générée de zéro ≤ 80 Ko ; logo ANMSM ou écusson Snow Explorer original ≤ 40 Ko ; plan uniquement par URL déjà disponible, jamais reconverti dans l’import. Les images originales sont réellement générées avec l’outil de génération, puis embarquées en WebP dans le JSON ; une photo officielle n’est jamais une cover de remplacement.

## Filtre des lots importables

Appliquer scripts/filter-complete-stations.py au lot final avant validation et livraison, après préparation des médias, avec un nouveau chemin de sortie et un rapport distinct. Ce contrôle élimine toute station sans un champ obligatoire, forfaits avancés de la bonne saison, cover, logo ou plan importable ; zéro est accepté. Relire aussi la validité factuelle et la complétude des tarifs : ce filtre de présence ne remplace pas la recherche ni la validation de schéma. Vérifier les données avant génération des médias pour éviter de produire inutilement des images de stations déjà incomplètes. Ne pas fractionner un lot pour respecter une ancienne règle de 1 Mo : cette restriction éditoriale est supprimée. Les plafonds unitaires WebP de 80/40 Ko restent inchangés ; le plafond technique courant de l’importeur se lit dans station_batch_contract.max_bytes.
