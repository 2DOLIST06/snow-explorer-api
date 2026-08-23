# Envoi IndexNow administrateur

`POST /api/admin/indexnow` utilise la session administrateur existante. Comme
toute écriture sous `/api/admin`, la requête doit transmettre le cookie de
session HttpOnly et le jeton renvoyé par `GET /api/admin/auth/session` dans
l'en-tête `X-CSRF-Token`.

Le service back-end doit définir `INDEXNOW_KEY`. Cette même clé doit être
publiquement accessible côté site à l'adresse :

```text
https://www.snow-explorer.com/<INDEXNOW_KEY>.txt
```

Exemple de corps :

```json
{
  "urls": [
    "https://www.snow-explorer.com/stations/val-thorens",
    "https://www.snow-explorer.com/stations/tignes"
  ]
}
```

Seules les URL HTTPS dont l'hôte exact est `snow-explorer.com` ou
`www.snow-explorer.com` sont acceptées. Une réponse réussie contient
`{"success": true, "submitted": 2}`. Les erreurs de validation et d'IndexNow
ont toujours `success: false` et un message `error` exploitable par le front.
