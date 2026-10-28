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
