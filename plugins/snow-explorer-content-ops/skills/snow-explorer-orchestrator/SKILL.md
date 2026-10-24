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
- rechercher au minimum `name` et `slug`, plus les principaux champs de localisation et d'identification lorsque disponibles ;
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

Pour toute création de station, appliquer impérativement [les règles de description](references/station-descriptions.md) : recherche des faits d’abord, rédaction originale ensuite, sans plan ni phrases préfabriqués ; relire fluidité et diversité avant livraison. Le périmètre exact des champs figure dans [le workflow par lots](references/station-batches.md). Ne pas enrichir automatiquement avec d’autres blocs.

## Médias de création

Appliquer obligatoirement [cover, écusson et plan](references/station-media.md) avant de produire un lot : cover générée de zéro ≤ 80 Ko ; logo ANMSM ou écusson Snow Explorer original ≤ 40 Ko ; plan uniquement par URL déjà disponible, jamais reconverti dans l’import. Les images originales sont réellement générées avec l’outil de génération, puis embarquées en WebP dans le JSON ; une photo officielle n’est jamais une cover de remplacement.
