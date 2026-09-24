# Import/export JSON des stations (administration)

## Architecture existante et choix d'intégration

Le modèle métier est `Resort`. Les pistes (`Piste`) et remontées (`Lift`) sont des
relations dédiées; les plans et le snowpark sont en partie des colonnes de
`Resort`; webcams, météo, neige, forfaits et options d'affichage sont conservés
dans `StationWidgets.config`. L'API d'administration historique utilise à la fois
`/api/admin/resorts` et `/api/admin/stations`. Le nouveau contrat est intégré à
`/api/admin/resorts`, sans modifier les routes publiques ni exporter le document
`StationWidgets` imbriqué. Il n'existait ni authentification Flask ni mécanisme
d'import JSON réutilisable (le script OpenSkiMap Node est un traitement hors API).

Les routes d'écriture sont protégées par la session administrateur et le jeton
CSRF. Les jetons de preview sont signés avec `RESORT_IMPORT_SECRET` lorsqu'il est
défini, puis avec `SECRET_KEY` ou `ADMIN_SESSION_SECRET` comme solutions de repli.
Le serveur refuse de signer sans aucun de ces secrets. `X-Admin-User` peut
identifier l'administrateur dans l'historique. Les anciennes routes
d'administration ne sont pas changées par ce lot; leur sécurisation globale doit
être traitée séparément pour éviter une rupture de compatibilité.

## Contrat 1.0

`SCHEMA_VERSION = "1.0"`. Un document unitaire contient `schema_version`,
`exported_at`, `station`, puis les blocs `pistes`, `remontees`, `snowpark`,
`webcams`, `meteo`, `snow` et `forfaits`. Un document multiple remplace les blocs
par `stations: [{station, ...}]`. Les dates utilisent `YYYY-MM-DD`, le timestamp
d'export est ISO 8601 UTC, et seules les URL HTTP(S) valides sont admises.
Les routes d'import multiple acceptent les deux formes : le document unitaire
avec `station` (une seule station) et le document multiple avec `stations` (une
ou plusieurs stations). Il est interdit de mélanger les deux formes dans un même
document.

`station` est une liste blanche des champs éditables de `Resort`. `pistes.items`
et `remontees.items` représentent les vraies relations. Un tableau `items` présent
et vide les vide; absent, il les conserve. Les compteurs agrégés des deux blocs
sont exportés pour lecture mais ne remplacent pas leurs relations. Les autres
blocs sont une projection plate et snake_case de la configuration réellement
utilisée; le blob widgets complet, secrets, jetons et timestamps techniques ne
sont jamais exportés.

### Absent, `null`, vide

* absent: aucune modification;
* `null`: effacement uniquement pour un champ nullable; `id: null` est accepté
  afin de retrouver la station par son `slug` (ou de générer un identifiant lors
  d'une création);
* `""`: devient `null` pour un texte facultatif, mais est refusé pour `slug` et
  `name`; jamais converti en zéro;
* objet absent: bloc conservé;
* `enabled: false`: seul `enabled` et les autres clés explicitement présentes
  changent;
* tableau absent: relation conservée; `pistes.items`, `remontees.items`,
  `webcams.items`, `forfaits.items` et `forfaits.columns` présents et vides sont
  des suppressions explicites.

## Routes

| Méthode | Route | Fonction |
|---|---|---|
| GET | `/api/admin/resorts/<id-ou-slug>/export` | export unitaire |
| GET | `/api/admin/resorts/export?active=true` | export stable global |
| GET | `/api/admin/resorts/import-template` | modèle sans donnée réelle |
| GET | `/api/admin/stations/import/template` | alias utilisé par le front pour le modèle |
| POST | `/api/admin/resorts/<id-ou-slug>/import/preview` | preview unitaire |
| POST | `/api/admin/resorts/<id-ou-slug>/import/confirm` | confirmation unitaire |
| POST | `/api/admin/resorts/import/preview` | preview multiple |
| POST | `/api/admin/resorts/import/confirm` | confirmation multiple |
| GET | `/api/admin/resorts/import-history` | 100 derniers imports |
| GET | `/api/admin/resorts/import-history/<id>` | détail d'historique |
| GET | `/api/admin/stations/imports/history` | alias utilisé par le front pour l'historique |

Les POST acceptent `multipart/form-data` (`file`), un corps JSON brut ou une
enveloppe JSON `{ "file": <document JSON>, "create_missing": true }` (la clé
`document` est également acceptée). Un objet JavaScript `File` ne doit pas être
passé directement à `JSON.stringify`, car il deviendrait `{}` sans transmettre
le contenu du fichier. Dans ce cas, l'API répond `file_content_missing`. La
confirmation renvoie exactement le même fichier et le `preview_token` dans le
formulaire, dans l'enveloppe JSON, ou via `X-Preview-Token`. Chaque station de la
prévisualisation contient `id`, `slug`, `name`, `status` et `changes`, afin de
pouvoir être affichée directement par l'interface. Le token HMAC couvre le
contenu canonique, la cible et toutes les options: un fichier ou mode différent
reçoit HTTP 409.

En multiple, `create_missing=false` et `all_or_nothing=true` sont les valeurs par
défaut. Les options sont des champs de formulaire ou paramètres de requête. La
résolution se fait par `id`, puis `slug`; une correspondance vers deux stations
différentes est un conflit. Aucune station absente du fichier n'est supprimée.

## Validation, sécurité et limites

Limites configurables: `RESORT_IMPORT_MAX_FILE_SIZE` (1 MiB),
`RESORT_IMPORT_MAX_STATIONS` (500), `RESORT_IMPORT_MAX_DEPTH` (20), et
`RESORT_IMPORT_MAX_ARRAY_ITEMS` (1000). JSON illisible: 400; fichier trop grand:
413; schéma/type/champ inconnu: 422. Slugs: 120 caractères maximum, minuscules,
chiffres et tirets. Coordonnées, nombres non négatifs, dates et URL sont validés.

Le HTML importé (`description_html` et `snowpark.description_html`) passe dans une
liste blanche serveur: `p`, `br`, `strong`, `em`, `ul`, `ol`, `li`, `a`, `h2`,
`h3`, `blockquote`; seul `href` HTTP(S) est conservé. Scripts, iframes, styles,
objets, embeds, événements et protocoles dangereux sont supprimés. Markdown est
laissé intact.

## Transactions, historique et rollback

Une confirmation est transactionnelle. En mode multiple non strict, les éléments
classés invalides/conflits sont ignorés et le statut devient `partial`. L'historique
stocke identifiant/date/admin/fichier/version/type/statut/cible, compteurs,
checksum, erreurs et diff avec anciennes valeurs, jamais le fichier complet.

Le rollback automatique n'est **pas exposé**: le schéma actuel n'a ni version de
ligne ni horodatage métier permettant de prouver qu'une station et ses blobs JSON
n'ont pas été modifiés depuis. L'historique permet un rétablissement audité, mais
écraser automatiquement un changement plus récent ne serait pas fiable.

### Exemples abrégés

Export: `{"schema_version":"1.0","exported_at":"2026-08-01T19:00:00+00:00","station":{"id":"example-id","slug":"example-slug","name":"Example station"},"pistes":{"enabled":false,"items":[]}}`.

Preview: `{"valid":true,"target":{"id":"...","slug":"auron","name":"Auron"},"changes":[{"path":"station.meta_title","old_value":"Avant","new_value":null,"action":"clear"}],"preview_token":"..."}`.

## Couverture auditée (ajouts compatibles 1.0)

La version reste `1.0` : les quatre blocs ajoutés sont facultatifs, donc les fichiers 1.0
antérieurs restent valides et leur absence ne modifie aucune donnée existante.

| Champ/bloc | Source enregistrée | Chemin JSON | Export/import unitaire et multiple |
|---|---|---|---|
| identité, publication, localisation, coordonnées, altitudes, totaux, saison, médias, descriptions principale et SEO | colonnes éditables de `Resort` | `station.*` | oui/oui |
| détail et compteurs pistes | `Piste`; compteurs calculés | `pistes.items`, `pistes.green|blue|red|black` | oui; les compteurs sont informatifs, `items` est modifiable |
| plan, activation et secours officiel | colonnes `Resort.pistes_*`; `StationWidgets.config.pistes` | `pistes.small_map_url|large_map_url|caption|enabled|official_map_url` | oui/oui |
| remontées et compteurs | `Lift`; compteurs calculés | `remontees.items`, `remontees.tire_fesses|telesieges|telepheriques` | oui; compteurs informatifs |
| description indépendante | `StationWidgets.config.description` | `description.enabled`, `description.html` | oui/oui; distinct de `station.description_html` |
| snowpark visuel | colonnes `Resort.snowpark_*`; widget `snowpark` | `snowpark.*` | oui/oui |
| forfaits historiques | widget `forfaits` | `forfaits.enabled|columns|items` | oui/oui, forme existante conservée |
| forfaits avancés | `SkiPassSeason`, `SkiPassPeriod`, `SkiPassProduct`, `SkiPassPrice` | `forfaits_avances.seasons[]` | oui/oui; contrat normalisé existant `periods`/`passes`/`prices` |
| domaines | `SkiAreaResort` vers `SkiArea` | `domaines_skiables.items[].id|slug` | oui/oui; aucune caractéristique de domaine dupliquée |
| webcams, météo, enneigement | widgets homonymes | `webcams`, `meteo`, `snow` | oui/oui |

Les exemples exhaustifs de démonstration sont
[`station-export-1.0.json`](examples/station-export-1.0.json) et
[`stations-export-1.0.json`](examples/stations-export-1.0.json). L'objet métier
dans `stations[]` est exactement produit par le même sérialiseur que l'export unitaire.

### Sémantique des nouveaux blocs

* bloc absent : conservation intégrale ; `null` : effacement de la propriété nullable
  explicitement fournie ; `false` et `0` sont conservés comme valeurs ;
* `forfaits_avances.seasons: []` supprime toutes les saisons avancées. Un tableau
  présent remplace l'ensemble, saison par saison, dans la transaction d'import ;
* `domaines_skiables.items: []` retire tous les rattachements. Un tableau présent
  remplace l'ensemble sans créer de domaine. Chaque référence est résolue avant écriture ;
* une référence de domaine inconnue ou un couple `id`/`slug` contradictoire est une
  erreur localisée. Les relations sont recréées sans doublon ;
* `pistes.items: []` et `remontees.items: []` vident leur relation ; les tableaux
  historiques de widgets présents et vides sont des suppressions explicites.

La création requiert `slug` et `name`; un `id` absent ou `null` est généré en UUID.
Pour une mise à jour, la résolution tente `id` et `slug`. S'ils désignent deux lignes,
ou si un slug existant est accompagné d'un autre identifiant, l'import est refusé.
La répétition du même fichier retrouve donc la même station et remplace les relations,
sans duplication.

La confirmation multiple utilise une transaction de fichier unique. Avec
`all_or_nothing=true` (défaut), toute erreur empêche toute écriture. Avec `false`, les
stations invalides sont ignorées mais toutes les écritures acceptées et l'historique
restent dans une même transaction. Les réponses de preview donnent les erreurs avec
leur chemin `stations.<index>...`; la confirmation donne les compteurs créés, mis à
jour, ignorés et échoués.

## Frontend

Ce dépôt ne contient pas le frontend Snow Explorer. Les quatre routes nécessaires sont
exposées sous les deux préfixes `/api/admin/resorts` et `/api/admin/stations`. Le front
doit transmettre le document sans reconstruire/filtrer ses blocs, afficher `errors[].path`
et `errors[].message`, puis renvoyer le même document et `preview_token` à la confirmation.
Il doit proposer `create_missing` sur la liste et respecter le résultat transactionnel.
