# Strict minimum — vérification front pour `admin/stations`

## Ce qui est déjà établi
- La page admin stations appelle directement `https://snow-explorer-api.onrender.com/api/admin/stations/` via `NEXT_PUBLIC_SKI_API_BASE`, pas `/api/ski/*`.
- Le `204` sur `HEAD https://www.snow-explorer.com/admin/stations` concerne le front (Vercel), pas l’API Render.

## Ce qu’il faut capturer côté navigateur prod (obligatoire)
Exécuter dans la console:

```js
const urls = [
  "https://snow-explorer-api.onrender.com/api/admin/stations",
  "https://snow-explorer-api.onrender.com/api/admin/stations/"
];

for (const url of urls) {
  for (const method of ["GET", "OPTIONS"]) {
    fetch(url, { method })
      .then(async r => {
        const t = await r.text();
        console.log({
          method,
          url: r.url,
          status: r.status,
          firstLine: (t || "").split("\n")[0],
          server: r.headers.get("server"),
          xPoweredBy: r.headers.get("x-powered-by")
        });
      })
      .catch(e => console.log({ method, url, error: String(e) }));
  }
}
```

Puis:

```js
console.log("NEXT_PUBLIC_SKI_API_BASE =", process.env.NEXT_PUBLIC_SKI_API_BASE);
console.log("NEXT_PUBLIC_API_URL =", process.env.NEXT_PUBLIC_API_URL);
```

## Interprétation rapide
- `404` + `Cannot GET ...` => mauvais service cible/proxy/routage.
- `200/30x/40x` JSON typé Flask => on tape bien l’API, on débugge ensuite le contrat.
- `OPTIONS` en échec => souci CORS/proxy avant logique métier.
