# Pages publiques de région

## Lecture publique

`GET /api/regions/:slug` renvoie les informations éditoriales de la région et
la liste `stations` de ses stations actives. Les stations sans slug public et
les stations désactivées sont exclues.

```json
{
  "id": "auvergne-rhone-alpes",
  "slug": "auvergne-rhone-alpes",
  "name": "Auvergne-Rhône-Alpes",
  "country_code": "FR",
  "description_html": "<p>Découvrez les stations de la région.</p>",
  "meta_title": "Stations de ski en Auvergne-Rhône-Alpes",
  "meta_description": "Toutes les stations de la région.",
  "updated_at": "2026-08-19T10:30:00+00:00",
  "stations": []
}
```

`GET /api/regions` renvoie un tableau des fiches françaises, avec `id`, `slug`,
`name`, `country_code`, `description_html`, `meta_title`, `meta_description` et
`updated_at`. Les identifiants et slugs sont explicites et canoniques : le
frontend peut sélectionner la fiche par slug sans déduire son identité du nom.
Le champ `seo_text` reste uniquement un alias administratif.

`updated_at` est l'horodatage ISO 8601 UTC de la dernière modification du
contenu éditorial de la région. Il est également présent dans `GET
/api/regions` afin que le générateur de sitemap n'ait pas à charger chaque
fiche séparément.

Une région inconnue renvoie un statut `404` et l'erreur `region_not_found`.

L'identifiant public canonique de PACA est
`provence-alpes-cote-d-azur`. L'API continue de reconnaître l'ancien
identifiant `provence-alpes-cote-dazur` dans les données existantes et renvoie
toujours l'identifiant canonique. Cette compatibilité évite qu'un déploiement
du correctif de slug doive être parfaitement synchronisé avec la migration des
stations déjà enregistrées.

## Éditeur d'administration

`GET /api/admin/regions` (avec ou sans slash final) renvoie un tableau des
régions existantes, triées par nom puis identifiant, avec les mêmes champs
éditoriaux que les fiches.

L'éditeur charge le contenu avec `GET /api/admin/regions/:slug`, puis
l'enregistre avec `PATCH /api/admin/regions/:slug`. Comme toutes les routes
`/api/admin/*`, ces routes utilisent la session administrateur et la protection
CSRF existantes.

Le `PATCH` accepte les propriétés facultatives suivantes :

- `description_html` ;
- `meta_title` ;
- `meta_description`.

Pour compatibilité avec le formulaire d'administration existant, `seo_text`
est accepté comme alias de `description_html`. Les GET administratifs (liste
et fiche) et la réponse PATCH renvoient les deux noms avec la même valeur.
Les réponses publiques utilisent `description_html` et n'exposent pas l'alias
administratif `seo_text`.
Si les deux propriétés sont envoyées avec des valeurs différentes, le PATCH
renvoie `400 {"error":"conflicting_content_fields"}` sans sauvegarde.

Le HTML est nettoyé côté serveur avec la même liste blanche que le contenu des
stations. Envoyer `null` permet d'effacer une propriété.

Le champ JSON `description_html` est lu et enregistré dans la colonne historique
`regions.seo_text`. Ce correctif ne nécessite aucune migration, création de
colonne ou copie de données.

### Contrat JSON du formulaire

La fiche est chargée avec `GET /api/admin/regions/{id}` : la réponse est
`{"region": {...}}`, avec `id`, `slug`, `name`, `country_code`, `description_html`,
`seo_text`, `meta_title`, `meta_description` et `updated_at`.

Le contrat canonique de sauvegarde est :

```http
PATCH /api/admin/regions/auvergne-rhone-alpes
Content-Type: application/json
X-CSRF-Token: <jeton de la session administrateur>
Cookie: admin_session=<session>
```

```json
{
  "meta_title": "Stations de ski en Auvergne-Rhône-Alpes",
  "meta_description": "Découvrez les stations de la région.",
  "description_html": "<h2>Les massifs</h2><p>Découvrez <strong>les Alpes</strong>.</p>"
}
```

Le formulaire existant peut continuer à envoyer `seo_text` à la place de
`description_html`, avec les mêmes protections et le même nettoyage HTML.
Réponse : `{"ok":true,"region":{...}}`. Les propriétés omises restent intactes.
Les limites physiques PostgreSQL sont 70 caractères pour `meta_title` et 170
pour `meta_description`. Le frontend applique déjà ces limites.

La création des fiches éditoriales manquantes est documentée dans
[Préparation des fiches régionales](region-page-preparation.md).
