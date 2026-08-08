# API publique des stations

Le conteneur de production démarre l'application Flask/Gunicorn. Sa route de
liste publique est :

```http
GET /api/resorts/?active=true&limit=6&q=Auron
```

Cette route ne requiert ni cookie, ni session, ni jeton, ni en-tête propre au
frontend. `active=true` est facultatif : la route publique exclut déjà, y
compris sans ce paramètre, les stations inactives, celles dont l'activation est
nulle et celles dont le slug est nul, vide ou composé d'espaces. Une autre
valeur de `active` produit une erreur `400`, car les stations non publiées ne
sont jamais exposées par cette route.

Paramètres :

- `q` : recherche facultative, insensible à la casse, dans le nom et le slug ;
- `active=true` : explicite le filtre public déjà appliqué ;
- `limit` : entier strictement positif, au maximum `200` (valeur par défaut :
  `200`).

Le tri est stable : nom croissant, puis identifiant croissant. Il s'agit d'un
tri technique, pas d'un classement de popularité. La région et l'image sont des
colonnes du modèle `Resort`, et non des relations chargées séparément ; la
sérialisation ne produit donc pas de requêtes N+1. Le contrat existant conserve
le nom `cover_image_url`.

Exemple de réponse (champs additionnels omis ici) :

```json
[
  {
    "id": "resort-1",
    "name": "Auron",
    "slug": "auron",
    "is_active": true,
    "region": {
      "id": "provence-alpes-cote-d-azur",
      "name": "Provence-Alpes-Côte d’Azur",
      "country_code": "FR"
    },
    "cover_image_url": "https://cdn.example.test/auron.jpg"
  }
]
```

Pour un fetch Next.js exécuté pendant le build ou côté serveur, définir
`API_URL` avec l'origine joignable du backend (par exemple
`http://snow-explorer-api:5001` sur le réseau privé), puis appeler
`${API_URL}/api/resorts/?active=true&limit=6`. Cette variable serveur ne doit
pas être remplacée par `NEXT_PUBLIC_API_URL` lorsque l'origine privée est
disponible. CORS ne s'applique pas aux fetchs serveur à serveur.

Les routes d'administration `/api/admin/resorts` et `/api/admin/stations`
restent distinctes et conservent leur comportement. L'ancienne implémentation
Node contient aussi l'alias `/api/ski/resorts`, mais cet alias ne fait pas partie
de l'application Flask démarrée par le `Dockerfile`.

## Fiche utilisée par `/stations/[slug]`

```http
GET /api/resorts/<slug>
```

`slug` est un segment de chemin obligatoire. Cette route est publique et ne
demande aucune authentification. Elle renvoie directement le DTO station (pas
d'enveloppe `resort`) avec les champs d'identité, localisation, médias, contenu
existant, altitudes, chiffres-clés, dates ISO `YYYY-MM-DD` et une configuration
publique sous `cfg`. Une station absente ou inactive renvoie `404` et
`{"error":"resort_not_found","message":"Station not found"}`. Les réponses
`200` portent `Cache-Control: public, max-age=300, s-maxage=3600`.

`region.name` provient en priorité de la table `region`, recherchée par
`resort.region_id`; la colonne historique `resort.region_name` est seulement le
repli si le référentiel ne contient pas cet identifiant. Aucune valeur n'est
inventée. Les chiffres-clés publics sont `ski_area_km`, `snowparks_count` et
`family_parks_count`. Pour éviter une migration, les deux derniers lisent
respectivement les colonnes historiques `pistes_count` et `lifts_count`; ces
anciens noms, ainsi que les compteurs par couleur de piste ou type de remontée,
ne sont plus exposés publiquement.

`season_open_date` et `season_close_date` contiennent la date complète avec son
année. `season_label` fournit les années prêtes à afficher dans un titre de type
« Saison 2026-2027 »; il vaut `null` lorsqu'une des deux dates manque.

La cause des anciennes chaînes `widgets.widgets` est le stockage d'une réponse
déjà enveloppée dans la propriété `widgets`, puis sa réutilisation comme
configuration lors de sauvegardes successives. Le DTO déroule les enveloppes
historiques `widgets`/`cfg`, puis applique une liste blanche. La réponse expose
exactement un objet `cfg` contenant `pistes`, `meteo`, `description`,
`forfaits`, `webcams`, `snow`, `snowpark`, `remontees` et `snowparks`; elle
n'expose ni clé `widgets`, ni configuration d'administration.

La sérialisation effectue un nombre constant de lectures (station, région,
configuration). Elle ne parcourt aucune relation et ne peut donc pas produire de
requête N+1. Les modèles actuels ne définissent pas de relations webcams,
snowparks ou forfaits : leurs seules données publiques éventuelles sont les
sections déjà stockées dans `cfg`.
