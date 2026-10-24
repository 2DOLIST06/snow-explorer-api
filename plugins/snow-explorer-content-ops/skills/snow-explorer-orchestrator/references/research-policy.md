# Politique RESEARCH Snow Explorer

## Priorité des sources

Utiliser en priorité, selon le champ et la disponibilité :

1. `official` : site officiel de la station ou de l'exploitant.
2. `ski_area_official` : site officiel du domaine skiable.
3. `government` : données publiques d'une autorité ou administration.
4. `tourism_official` : office de tourisme ou organisme touristique officiel.
5. `map_reference` : source cartographique de référence adaptée au champ.
6. `reputable_reference` : base ou média spécialisé reconnu.
7. `secondary` : source secondaire utile pour recoupement, mais pas prioritaire si une source officielle existe.

Ne pas attribuer de score numérique artificiel de confiance.

## Règles par champ

- Identité, site officiel, dates de saison : privilégier la station/exploitant officiel.
- Altitudes, pistes, remontées, kilomètres skiables : privilégier station ou domaine officiel; recouper en cas d'écart notable.
- Région, département, coordonnées : préférer données publiques officielles ou référence cartographique fiable.
- Domaines skiables : ne proposer une relation que si l'appartenance est explicitement établie.
- Médias : appliquer station-media.md. Les visuels officiels servent d’inspiration à une cover originale générée de zéro. Le logo est celui de l’ANMSM si disponible, sinon un écusson Snow Explorer original. Un écusson ne doit jamais être présenté comme le logo officiel. Le plan reste une URL déjà disponible ; aucune URL de stockage n’est inventée.
- Contenus éditoriaux : distinguer les faits sourcés de la rédaction originale. Ne pas copier de longs passages protégés.

## Choisir une valeur cohérente

En présence de plusieurs chiffres, faire l’arbitrage soi-même. Préférer la source la plus directement responsable du champ (exploitant pour pistes, remontées, tarifs et dates), concernant la bonne saison et le bon périmètre. Vérifier si l’écart vient des tapis, secteurs, domaines communs, itinéraires ou anciennes éditions. À priorité comparable, retenir la publication la plus récente et la mieux détaillée ; ne pas compter les reprises d’une même source comme des preuves indépendantes.

Inclure la valeur retenue et conserver les sources ainsi que la raison du choix dans research. Utiliser verified lorsque l’arbitrage est étayé, sans prétendre que toutes les sources concordent. Ne pas omettre un chiffre uniquement parce qu’un annuaire en donne un autre. Ne pas faire de moyenne, de vote mécanique ni d’addition de catégories de périmètres différents. Réserver conflicting ou ambiguous aux cas où aucune décision factuelle raisonnable n’est possible ; une donnée vraiment introuvable reste omise et jamais inventée. Ne pas livrer une liste de signalements à traiter ou solliciter des arbitrages routiniers : la station fera l’objet d’une revue globale ultérieure.

## Saisonnalité

Les données de saison, forfaits et dates peuvent changer chaque année. Toujours conserver `observed_at` et éviter de mélanger des saisons différentes.

## Exhaustivité d'un pays

Pour détecter des stations absentes, utiliser plusieurs sources de couverture lorsque nécessaire. Une seule liste commerciale ou communautaire ne suffit pas à affirmer qu'un pays est exhaustivement couvert.
