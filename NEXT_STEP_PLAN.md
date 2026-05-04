# Plan immédiat (1 seule passe)

## Verdict actuel
- Le front appelle bien `https://snow-explorer-api.onrender.com/api/admin/stations/`.
- Le `204` vu sur `https://www.snow-explorer.com/admin/stations` est normal (front), et n’aide pas pour l’API.
- Le blocage principal est maintenant **déploiement/routage API** (404 côté Render), pas la logique bouton.

## Ce que TU fais maintenant (dans cet ordre)
1. **Redéployer le backend Render** sur le commit le plus récent.
2. Dès que le deploy est `Live`, tester dans un navigateur:
   - `https://snow-explorer-api.onrender.com/api/admin/stations/`
3. Si c’est encore 404: ouvrir Render > service API > Logs, puis filtrer `GET /api/admin/stations/`.
4. Copier les 20 lignes de logs autour de la requête.

## Ce que JE ferai avec ces logs
- Si la requête n’apparaît pas: DNS/proxy mauvais service.
- Si elle apparaît avec 404 Flask: route non chargée au boot (import/entrypoint).
- Si 200: on passe au contrat front (`is_active` vs mapping UI).
