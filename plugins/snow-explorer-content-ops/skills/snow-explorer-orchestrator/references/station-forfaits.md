# Forfaits avancés : grilles et onglets

## Structure existante à conserver

Utiliser `forfaits_avances.seasons[]` avec `season`, `currency`, `source_url`, `periods`, `passes` et `prices` dans chaque forfait. Lire le contrat courant avant construction. Les exports fournis par l’utilisateur servent de référence de structure, jamais de source de prix pour une autre station.

Dans ce modèle, chaque entrée `periods[]` définit un groupe de tarifs visible dans le sélecteur public (appelé « onglet » par l’utilisateur). Ce n’est pas seulement une période du calendrier. Le front filtre les lignes à partir de `prices[].period_id`.

- Conserver l’année dans `season`, par exemple `2026-2027`.
- Créer un groupe par famille d’offre réellement distincte : domaine complet, domaine étendu, accès débutant ou mini-domaine, piéton, randonnée, forfait saison, famille ou autres produits publiés. N’ajouter que les familles effectivement proposées par la station.
- Regrouper dans une même grille les durées et catégories d’une même famille ; ne pas créer un groupe pour chaque durée ou tranche d’âge.
- Ne jamais réunir ski complet, débutants, piétons et abonnements dans un groupe générique « Hiver 2026–2027 ».
- Si la source distingue des périodes tarifaires pour une même famille, les représenter sans mélanger les familles, avec un intitulé court qui distingue offre et période : « Domaine complet — vacances », par exemple.
- Des groupes différents peuvent avoir les mêmes dates. Cela ne justifie pas leur fusion.
- Employer des identifiants uniques et stables, un `sort_order` explicite et des noms de forfait qui indiquent le produit ou sa durée, plutôt que le seul nom de la station répété sur chaque ligne.
- Rattacher chaque tarif à l’identifiant du groupe adéquat avec `period_id`. Vérifier que toutes les références existent et que chaque groupe possède des prix.

## Intitulés et dates

Donner des noms courts et compréhensibles : « Domaine complet », « Premières traces — Planay et Cuvy », « Piéton », uniquement si ces offres sont établies par les sources. Ces exemples ne constituent pas une liste fixe de groupes à créer pour chaque station.

Ne pas ajouter dans les noms des groupes, des forfaits, des catégories ou dans leurs notes publiques : « selon enneigement », « constaté le… », « vérifié le… », « fenêtre attestée depuis la consultation », « durée commerciale non précisée » ni commentaires sur la méthode de recherche.

Une prévente officielle peut former un groupe distinct « Saison — prévente » avec ses conditions d’achat utiles, lorsqu’elles sont publiées. Une date de consultation n’est jamais une date de début ou de fin commerciale. Ne pas créer de période d’un jour uniquement parce qu’un prix a été observé ce jour-là. Distinguer dates de pratique et fenêtre d’achat selon la source et le contrat ; ne pas inventer de bornes pour remplir des champs requis. Si une offre ne peut pas être représentée fidèlement, conserver le constat dans `research` et appliquer la règle de complétude sans fabriquer de période.

## Prix, conditions et provenance

Rechercher toutes les familles et durées publiées dans la grille officielle et la billetterie, sans se limiter à la première offre mise en avant. Conserver les catégories, âges, durées, montants fixes ou fourchettes dynamiques et restrictions vérifiés ; ne pas modifier les prix pour réorganiser les groupes.

Réserver `note` aux conditions utiles au tarif concerné : justificatif, support payant, restriction d’accès ou composition du groupe, si publiés. Ne pas recopier toutes les conditions de toute la grille dans chaque cellule : une condition de prolongation ne décrit pas un forfait de quatre heures. Ne pas créer de condition plausible non publiée.

Mettre les liens de provenance dans `source_url` et `research`, et les dates de consultation uniquement dans `research`. Ne pas ajouter de phrase « Source : … » dans les intitulés ou notes publiques générés. Pour une reprise fidèle d’un export existant, conserver les notes historiques sans perte selon la règle de compatibilité de `station-batches.md` ; cette compatibilité n’autorise pas à produire de nouveaux commentaires de recherche.

## Contrôle du JSON rédigé

Avant validation technique et livraison :

1. Dresser à partir des sources l’inventaire des familles, durées et catégories ; le comparer aux groupes réellement construits.
2. Lire les noms de `periods` comme la liste que verra le visiteur. Corriger toute grille générique qui mélange plusieurs familles.
3. Contrôler chaque `period_id`, l’absence de groupe vide et la présence des prix dans la bonne grille. Vérifier que les durées ne sont pas masquées par des noms de lignes identiques.
4. Relire tous les textes publics pour retirer les commentaires de consultation et conditions sans rapport avec la ligne.
5. Comparer les montants, catégories, conditions et dates à la source, puis appeler la validation du contrat courant. Une validation de schéma seule ne garantit ni le bon classement ni la validité commerciale.

Ne pas annoncer une vérification de l’affichage réel si seule la structure JSON a été contrôlée. Cette évolution concerne la génération : elle ne réécrit pas les forfaits déjà importés.
