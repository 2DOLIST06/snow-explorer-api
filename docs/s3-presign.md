# Présignature des uploads S3

## Contrat HTTP

`POST /api/s3/presign` est une route administrateur. Elle requiert le cookie de
session HttpOnly existant, envoyé avec `credentials: "include"`, et le header
`X-CSRF-Token` renvoyé par l'authentification administrateur. `OPTIONS` reste
accessible sans session et sans CSRF.

Le corps doit être un objet JSON avec les deux chaînes obligatoires suivantes :

```json
{
  "filename": "logo-test.webp",
  "content_type": "image/webp"
}
```

L'extension, lorsqu'elle permet de déduire un type MIME, doit correspondre à
`content_type`. La réponse fournit une URL PUT valable 3 600 secondes, l'URL
publique finale et les headers qui doivent être reproduits pendant l'upload :

```json
{
  "uploadUrl": "URL_SIGNEE",
  "publicUrl": "https://bucket.s3.eu-west-3.amazonaws.com/uploads/identifiant.webp",
  "contentType": "image/webp"
}
```

L'upload PUT ne définit aucune ACL. Le bucket doit donc fournir l'accès public
final selon sa propre policy, ou `AWS_S3_PUBLIC_URL` doit désigner le CDN prévu.

## Variables d'environnement

À configurer sur Render sans jamais consigner leur valeur dans les logs :

* `AWS_ACCESS_KEY_ID`
* `AWS_SECRET_ACCESS_KEY`
* `AWS_REGION`
* `AWS_S3_BUCKET` (`AWS_BUCKET_NAME` reste accepté temporairement pour compatibilité)
* `AWS_S3_PUBLIC_URL` (optionnelle, URL publique de bucket ou CDN)
* `S3_ALLOWED_ORIGINS` (liste séparée par des virgules; production :
  `https://www.snow-explorer.com`)

La présignature réutilise les sessions administrateur. En production cross-site,
elle requiert donc aussi `ADMIN_SESSION_SECRET`, `ADMIN_SESSION_COOKIE_NAME`,
`ADMIN_COOKIE_SECURE=true`, `ADMIN_COOKIE_SAMESITE=None` et un frontend utilisant
`credentials: "include"`.

Ajouter `https://snow-explorer.com`, `http://localhost:3000` ou une preview
Vercel à `S3_ALLOWED_ORIGINS` uniquement si le déploiement correspondant doit
réellement appeler l'API. Les previews ne sont pas acceptées par wildcard.

## Vérifications curl

Préflight local :

```bash
curl -i -X OPTIONS "http://localhost:5001/api/s3/presign" \
  -H "Origin: https://www.snow-explorer.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,x-csrf-token"
```

POST local (avec des identifiants AWS de développement fournis uniquement dans
l'environnement du processus) :

```bash
curl -i -X POST "http://localhost:5001/api/s3/presign" \
  -H "Origin: https://www.snow-explorer.com" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: TOKEN_DE_TEST" \
  -b "admin_session=COOKIE_DE_TEST" \
  --data '{"filename":"logo-test.webp","content_type":"image/webp"}'
```

Préflight de production :

```bash
curl -i -X OPTIONS \
  "https://snow-explorer-api-3.onrender.com/api/s3/presign" \
  -H "Origin: https://www.snow-explorer.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,x-csrf-token"
```

POST de production :

```bash
curl -i -X POST \
  "https://snow-explorer-api-3.onrender.com/api/s3/presign" \
  -H "Origin: https://www.snow-explorer.com" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: TOKEN_DE_TEST" \
  -b "admin_session=COOKIE_DE_TEST" \
  --data '{"filename":"logo-test.webp","content_type":"image/webp"}'
```
