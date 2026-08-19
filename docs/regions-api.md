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

L'éditeur charge le contenu avec `GET /api/admin/regions/:slug`, puis
l'enregistre avec `PATCH /api/admin/regions/:slug`. Comme toutes les routes
`/api/admin/*`, ces routes utilisent la session administrateur et la protection
CSRF existantes.

Le `PATCH` accepte uniquement les propriétés facultatives suivantes :

- `description_html` ;
- `meta_title` ;
- `meta_description`.

Le HTML est nettoyé côté serveur avec la même liste blanche que le contenu des
stations. Envoyer `null` permet d'effacer une propriété.
