# Ski nordique (ajout compatible)

Les stations disposent d'un bloc optionnel `nordique` dans StationWidgets.config.
Aucune migration ni réécriture des données existantes n'est nécessaire. Les valeurs
`station.ski_area_km`, `station.pistes_count`, les couleurs et remontées actuelles
restent celles du ski alpin.

Le bloc est accepté dans les imports JSON 1.0 unitaires, multiples et les lots de
création ; il est inclus dans les exports lorsqu'il a été renseigné. Son absence
préserve les données nordiques en place. Les mises à jour sont partielles.

Exemple de fragment à ajouter au document d'import :

```json
"nordique": {
  "ski_area_km": 32.5,
  "pistes_count": 8,
  "green": 2,
  "blue": 3,
  "red": 2,
  "black": 1,
  "classic": true,
  "skating": true,
  "classic_km": 32.5,
  "skating_km": 25
}
```

Les comptes sont des entiers positifs ou nuls ; les kilomètres acceptent les
décimales. Une valeur inconnue peut être omise ou nulle. Ne pas additionner
classique et skating : les deux techniques peuvent partager les mêmes parcours.
Les couleurs sont facultatives et doivent suivre les informations de l'exploitant,
les classifications pouvant varier selon le pays.

Importer une distance ou un nombre strictement positif, ou une technique activée,
active automatiquement `enabled`. Le bouton admin permet de masquer le bloc sans
effacer ses valeurs. Un bloc activé sans données utiles reste invisible.

L'affichage public présente séparément le ski alpin (uniquement si des données
alpines positives existent) et le ski nordique. Le filtrage de complétude des lots
accepte une station exclusivement nordique sans exiger de chiffres alpins fictifs.

## Plan nordique indépendant

Le même bloc accepte `small_map_url`, `large_map_url`, `official_map_url` et
`caption` (facultatifs et effaçables avec `null`). Les URL doivent utiliser HTTP
ou HTTPS, sans identifiants. L'import d'un plan active le bloc nordique. L'image
est prioritaire sur le lien officiel ; aucune valeur ne remplace le plan alpin.
L'admin permet de charger une image (grand format + miniature), ou de saisir
les URL et la légende. Le public montre un sélecteur Domaine alpin / Domaine
nordique uniquement lorsque les deux plans sont disponibles, avec un seul plan
affiché à la fois. Un plan nordique seul est affiché directement.

## Présentation des indicateurs

Si les deux disciplines ont des indicateurs, le sélecteur propose Tout le domaine
(valeur initiale), Alpin et Nordique. Les deux sections restent dans le HTML rendu
sur le serveur ; le choix applique seulement l'attribut HTML `hidden` à la section
masquée. Chaque titre est regroupé avec ses indicateurs. Les techniques confirmées
(Classique et/ou Skating) sont affichées sous Techniques de ski avec une coche
verte ; aucune absence n'est signalée par une coche rouge.
