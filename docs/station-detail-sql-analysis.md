# Analyse SQL des fiches station

## Pourquoi deux implémentations existent

`GET /api/stations/<slug>` était initialement un alias de
`GET /api/resorts/<slug>`. Le commit `cedcbd6` (ajout des forfaits normalisés à
la fiche station) lui a donné son propre constructeur afin d'ajouter la grille
normalisée et le compteur de snowparks sans modifier le contrat historique de
`/api/resorts/<slug>`. Les deux routes ont ensuite continué à évoluer
séparément. Elles ne sont donc plus des alias JSON, malgré la documentation
historique qui les présentait ainsi.

## Contrats et comportements actuels

Les deux routes filtrent `resort.is_active`, renvoient un objet JSON direct en
`200`, le même en-tête de cache, et une grille `ski_pass` active ou `null`.
Elles divergent toutefois sur les points suivants :

| Sujet | `/api/resorts/<slug>` | `/api/stations/<slug>` |
|---|---|---|
| DTO de base | Liste blanche dédiée dans `get_public_resort` | `Resort.to_dict()`, complété par `_resort_public_dict` |
| Widgets | `cfg` public nettoyé (neuf sections) | pas de `cfg`; lit seulement `snowparks.count` vers `snowparks_count` |
| Région | nom de la table `region`, avec repli sur `resort.region_name` | valeurs historiques de la ligne `resort`, avec `country_code` |
| Champs propres | compteurs avec repli SQL, dates, cartes principales | coordonnées, `amenities`, légendes de cartes, `updated_at`, `resort_is_active` |
| `id` | explicitement converti en chaîne | valeur produite par le modèle (actuellement une chaîne) |
| 404 | erreur `resort_not_found` | erreur `station_not_found` |
| 500 | erreur « Unable to retrieve stations » | erreur « Unable to retrieve station » |

Même lorsque les champs de forfait ont aujourd'hui presque la même forme, ils
sont sérialisés par deux fonctions différentes. Une mutualisation implicite
pourrait donc aussi faire dériver ce sous-contrat.

Ce dépôt ne contient pas le frontend Next.js permettant d'identifier son appel
réel. `docs/public-resorts-api.md` désigne les deux chemins pour la page station,
tandis que l'historique de code qualifie `/api/stations/<slug>` d'alias utilisé
par les pages publiques. Cela constitue un indice, pas une preuve vérifiable
depuis ce dépôt. `server.js` est une autre implémentation backend et ne permet
pas davantage de conclure sur le client actuellement déployé.

## Nombre de lectures après la correction N+1

Pour une réponse nominale :

* `/api/stations/<slug>` fait **6 requêtes** constantes : station (1), widgets
  (1), puis saison/périodes/produits/prix normalisés préchargés (4). Une 404 ne
  fait qu'une requête.
* `/api/resorts/<slug>` fait **6 à 9 requêtes** : station (1), région (0 ou 1),
  widgets (1), forfait normalisé (4), et compteurs de secours pistes/remontées
  (0 à 2). Le cas courant avec région référencée et compteurs stockés en fait 7.
  Une 404 ne fait qu'une requête.

Ces nombres sont constants vis-à-vis du nombre de périodes, produits et prix :
la correction N+1 reste intacte.

## Fusion SQL envisageable pour `/api/resorts/<slug>`

Une seule requête avec `LEFT JOIN region` et `LEFT JOIN station_widgets` peut
charger la station, le nom de région et les widgets. Les compteurs de secours
peuvent être ajoutés par deux sous-requêtes corrélées `COUNT(*)`, ou par des
sous-requêtes agrégées jointes. Il ne faut pas joindre directement pistes,
remontées et widgets puis compter sans précaution : le produit cartésien
fausserait les deux compteurs.

Cette fusion peut ramener la partie station/région/widgets/compteurs de 3–5
lectures à 1, puis laisser les quatre lectures bornées des forfaits, soit **5
requêtes** au total. Elle doit sélectionner les colonnes du DTO explicitement
et conserver la règle « compteur stocké valide prioritaire, sinon COUNT ».

## Stratégie de mutualisation sans rupture

Il n'est pas sûr de remplacer une route par l'autre : leurs JSON et leurs 404
diffèrent déjà. La stratégie sûre est de mutualiser d'abord des briques
internes, sans changer les façades :

1. créer un chargeur commun retournant un objet interne typé (station, région,
   widgets, compteurs, forfait normalisé) ;
2. garder deux sérialiseurs de contrat et deux gestionnaires d'erreur ;
3. ajouter des tests instantanés exhaustifs pour chacun des deux JSON ;
4. migrer le frontend vers un contrat canonique seulement après inventaire de
   ses appels, avec une période de compatibilité ;
5. supprimer l'ancien sérialiseur dans un changement ultérieur versionné.

La mutualisation n'est donc volontairement pas réalisée dans ce changement.

