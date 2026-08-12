# Proxy Next.js requis pour la page `/forfaits`

La page publique est servie par le projet **frontend** déployé sur Vercel. Une
réponse contenant `x-matched-path: /404` et `Content-Type: text/html` signifie
que la requête n'a jamais atteint cette API : la route Next.js manque dans le
build du frontend. Déployer uniquement ce dépôt backend ne peut donc pas
corriger cette réponse.

## Route à ajouter au dépôt frontend

Pour un frontend utilisant le Pages Router, créer exactement :

```text
pages/api/ski/stations/[slug]-widgets.ts
```

avec le contenu suivant (adapter seulement le nom de la variable d'origine si
le projet en utilise déjà une autre) :

```ts
import type { NextApiRequest, NextApiResponse } from "next";

const API_ORIGIN = process.env.SKI_API_BASE ?? process.env.NEXT_PUBLIC_SKI_API_BASE;

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "method_not_allowed" });
  }

  const slug = Array.isArray(req.query.slug) ? req.query.slug[0] : req.query.slug;
  if (!slug || !API_ORIGIN) {
    return res.status(500).json({ error: "proxy_not_configured" });
  }

  try {
    const upstream = await fetch(
      `${API_ORIGIN.replace(/\/$/, "")}/api/stations/${encodeURIComponent(slug)}/widgets`,
      { headers: { Accept: "application/json" } },
    );
    const body = await upstream.text();
    res.status(upstream.status);
    res.setHeader("Content-Type", upstream.headers.get("content-type") ?? "application/json");
    res.setHeader("Cache-Control", "no-store");
    return res.send(body);
  } catch {
    return res.status(502).json({ error: "widgets_upstream_unavailable" });
  }
}
```

`SKI_API_BASE` doit être configurée dans le projet Vercel avec l'origine du
backend Render. Une variable serveur est préférable, car cette valeur n'a pas
besoin d'être intégrée au bundle du navigateur.

## Vérification après redéploiement du frontend

```bash
curl -i https://www.snow-explorer.com/api/ski/stations/auron-widgets
```

Le résultat attendu est du JSON avec un statut `200` (ou un `404` JSON si la
station est absente/inactive). Il ne doit plus contenir
`x-matched-path: /404`, `Content-Type: text/html`, ni une page 404 Vercel.

Vérifier ensuite les quatre slugs observés dans le navigateur :

```bash
for slug in auron isola-2000 la-clusaz val-thorens; do
  curl -fsS "https://www.snow-explorer.com/api/ski/stations/${slug}-widgets"
done
```

Le fichier doit être commité dans le dépôt frontend puis ce commit doit être
celui effectivement déployé par Vercel. La présence de la route correspondante
dans ce dépôt backend ne crée pas automatiquement une route API dans Next.js.
