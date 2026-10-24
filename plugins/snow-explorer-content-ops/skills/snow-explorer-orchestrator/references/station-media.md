# Cover, écusson et plan des pistes

Vérifier d’abord que station_batch_contract expose media_policy_version="2.0", generated_media et prepared_anmsm_maps. Si le backend expose encore l’ancien contrat, ne pas revenir à une photographie officielle ni à la conversion des plans : ce déploiement doit être terminé avant le nouveau test.

Ces règles remplacent toute ancienne règle de récupération d’une photographie officielle comme couverture ou de conversion de plan dans l’import par lots.

## Cover

Créer une image originale de zéro avec l’outil de génération d’images. Examiner d’abord les visuels officiels de la station pour s’inspirer du paysage, de l’ambiance et des éléments réellement présents. Faire une composition nouvelle : ne pas réutiliser, recadrer, filtrer ni simplement retoucher une photographie officielle pour la publier comme couverture. Ne pas inventer un lieu reconnaissable ou une installation technique. La création reste un visuel illustratif ; sa provenance générée et les sources d’inspiration sont conservées dans research. Ne pas ajouter de texte ou de logo à la cover. Livrer un WebP de 80 Ko maximum, proportions conservées et qualité vérifiée. Aucune URL de photographie officielle, ANMSM comprise, ne peut remplacer cette création.

## Logo / icône

Vérifier réellement la disponibilité du logo ANMSM pour la bonne station. S’il est disponible, utiliser celui-ci. Une erreur réseau n’est pas une absence.

S’il n’est pas disponible, créer avec l’outil de génération un écusson Snow Explorer original dans le style validé. Ne pas chercher un logo officiel ailleurs en remplacement. Référence de style retrouvée : écusson Brides-les-Bains ; double contour bleu marine/blanc, paysage propre à la station au rendu photographique, grand nom exact de la station en blanc sur bandeau bleu marine, bandeau secondaire « SNOW EXPLORER », ensemble lisible en petit format. Adapter uniquement le nom et le paysage, garder la cohérence visuelle. Préserver la transparence si elle existe. C’est une création Snow Explorer, jamais un logo officiel. La référence retrouvée est textuelle : ne pas prétendre avoir consulté une image modèle absente. Livrer un WebP de 40 Ko maximum, y compris pour le logo ANMSM.

## Transport des créations

La génération a lieu dans ChatGPT avant livraison du JSON. L’API d’import ne génère aucune image et ne remplace pas une génération manquante par une photo officielle.

Après génération réelle, utiliser scripts/embed-generated-media.py avec le fichier JSON du lot et les fichiers locaux générés : --cover slug=/chemin/creation.png et, seulement pour le remplacement ANMSM, --logo slug=/chemin/ecusson.png. Le script optimise les WebP et les embarque dans media[].data_base64 avec source_kind=generated. Les fichiers locaux doivent provenir de l’outil de génération, pas des sources d’inspiration. Relire le rendu compressé, l’orthographe et la lisibilité de l’écusson. Conserver le prompt réellement utilisé, les sources d’inspiration et la provenance ; ne pas inventer de preuve de génération. Ne pas livrer un faux chemin local, un placeholder base64, une URL sandbox ou une URL publique inventée comme source importable. Si l’outil de génération ou l’accès aux octets de son résultat est indisponible, annoncer cette limite concrète et ne pas présenter le lot comme prêt.

## Plans

Les plans sont uniquement des liens. Consulter prepared_anmsm_maps du contrat et réutiliser l’image déjà convertie par l’outil ANMSM, avec son URL persistante. Ne pas modifier l’outil ANMSM ni lancer sa conversion depuis l’import par lots. En l’absence de plan pertinent déjà préparé, utiliser le lecteur officiel (LumiPlay, Calaméo ou autre) ou un lien image existant. Aucun fichier de plan n’est généré, converti, réhébergé ni embarqué en base64. Les contrôles de disponibilité ne sont pas un téléchargement pour stockage. Le rendu doit être testé dans la modale lorsque le navigateur le permet, sans annoncer de réussite fictive.
