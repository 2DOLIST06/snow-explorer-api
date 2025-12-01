# Auto-generated: create or update French ski stations
$ErrorActionPreference = "Stop"
$api = "http://127.0.0.1:5001/api/admin/stations/"

$stations = @(
  @{ name = "Chamonix-Mont-Blanc"; slug = "chamonix-mont-blanc"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.923700; longitude = 6.869400 }
  @{ name = "Megève"; slug = "megeve"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.854300; longitude = 6.613100 }
  @{ name = "Saint-Gervais-les-Bains"; slug = "saint-gervais-les-bains"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.894000; longitude = 6.713000 }
  @{ name = "Les Houches"; slug = "les-houches"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.891000; longitude = 6.800000 }
  @{ name = "Les Contamines-Montjoie"; slug = "les-contamines-montjoie"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.820000; longitude = 6.726000 }
  @{ name = "Les Gets"; slug = "les-gets"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.158000; longitude = 6.666000 }
  @{ name = "Morzine"; slug = "morzine"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.182000; longitude = 6.703000 }
  @{ name = "Avoriaz"; slug = "avoriaz"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.191000; longitude = 6.775000 }
  @{ name = "Flaine"; slug = "flaine"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.000000; longitude = 6.692000 }
  @{ name = "Samoëns"; slug = "samoens"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.082000; longitude = 6.728000 }
  @{ name = "Le Grand-Bornand"; slug = "le-grand-bornand"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.941000; longitude = 6.433000 }
  @{ name = "La Clusaz"; slug = "la-clusaz"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.903000; longitude = 6.433000 }
  @{ name = "Les Carroz d'Arâches"; slug = "les-carroz-daraches"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.029000; longitude = 6.637000 }
  @{ name = "Praz de Lys - Sommand"; slug = "praz-de-lys-sommand"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.152000; longitude = 6.565000 }
  @{ name = "Les Brasses"; slug = "les-brasses"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.177000; longitude = 6.426000 }
  @{ name = "La Chapelle-d'Abondance"; slug = "la-chapelle-dabondance"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.291000; longitude = 6.795000 }
  @{ name = "Châtel"; slug = "chatel"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.269000; longitude = 6.840000 }
  @{ name = "Les Saisies"; slug = "les-saisies"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.758000; longitude = 6.537000 }
  @{ name = "Val Thorens"; slug = "val-thorens"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.297000; longitude = 6.583000 }
  @{ name = "Les Menuires"; slug = "les-menuires"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.324000; longitude = 6.541000 }
  @{ name = "Méribel"; slug = "meribel"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.397000; longitude = 6.565000 }
  @{ name = "Courchevel"; slug = "courchevel"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.415000; longitude = 6.634000 }
  @{ name = "Valmorel"; slug = "valmorel"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.482000; longitude = 6.456000 }
  @{ name = "La Plagne"; slug = "la-plagne"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.507000; longitude = 6.681000 }
  @{ name = "Les Arcs"; slug = "les-arcs"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.572000; longitude = 6.779000 }
  @{ name = "Tignes"; slug = "tignes"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.470000; longitude = 6.907000 }
  @{ name = "Val d'Isère"; slug = "val-disere"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.448000; longitude = 6.980000 }
  @{ name = "La Rosière"; slug = "la-rosiere"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.626000; longitude = 6.850000 }
  @{ name = "Sainte-Foy Tarentaise"; slug = "sainte-foy-tarentaise"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.587000; longitude = 6.892000 }
  @{ name = "Valloire"; slug = "valloire"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.165000; longitude = 6.430000 }
  @{ name = "Val Cenis"; slug = "val-cenis"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.288000; longitude = 6.882000 }
  @{ name = "Aussois"; slug = "aussois"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.239000; longitude = 6.744000 }
  @{ name = "La Norma"; slug = "la-norma"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.224000; longitude = 6.698000 }
  @{ name = "Les Karellis"; slug = "les-karellis"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.225000; longitude = 6.408000 }
  @{ name = "Les Sybelles (Le Corbier)"; slug = "les-sybelles-le-corbier"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.250000; longitude = 6.270000 }
  @{ name = "Les Sybelles (Saint-Sorlin-d'Arves)"; slug = "les-sybelles-saint-sorlin-darves"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.220000; longitude = 6.230000 }
  @{ name = "Pralognan-la-Vanoise"; slug = "pralognan-la-vanoise"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.379000; longitude = 6.720000 }
  @{ name = "Alpe d'Huez"; slug = "alpe-dhuez"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.091000; longitude = 6.069000 }
  @{ name = "Les 2 Alpes"; slug = "les-2-alpes"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.006000; longitude = 6.124000 }
  @{ name = "Chamrousse"; slug = "chamrousse"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.111000; longitude = 5.877000 }
  @{ name = "Les 7 Laux"; slug = "les-7-laux"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.291000; longitude = 5.982000 }
  @{ name = "Vaujany"; slug = "vaujany"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.157000; longitude = 6.146000 }
  @{ name = "Oz-en-Oisans"; slug = "oz-en-oisans"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.131000; longitude = 6.073000 }
  @{ name = "Villard-de-Lans"; slug = "villard-de-lans"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.065000; longitude = 5.555000 }
  @{ name = "Autrans-Méaudre"; slug = "autrans-meaudre"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.171000; longitude = 5.536000 }
  @{ name = "Serre Chevalier"; slug = "serre-chevalier"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.945000; longitude = 6.559000 }
  @{ name = "Montgenèvre"; slug = "montgenevre"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.936000; longitude = 6.726000 }
  @{ name = "Vars"; slug = "vars"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.576000; longitude = 6.700000 }
  @{ name = "Risoul"; slug = "risoul"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.616000; longitude = 6.635000 }
  @{ name = "Puy-Saint-Vincent"; slug = "puy-saint-vincent"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.825000; longitude = 6.490000 }
  @{ name = "Orcières-Merlette"; slug = "orcieres-merlette"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.690000; longitude = 6.325000 }
  @{ name = "La Grave - La Meije"; slug = "la-grave-la-meije"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 45.045000; longitude = 6.305000 }
  @{ name = "Pra Loup"; slug = "pra-loup"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-de-Haute-Provence"; latitude = 44.347000; longitude = 6.635000 }
  @{ name = "Le Sauze"; slug = "le-sauze"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-de-Haute-Provence"; latitude = 44.387000; longitude = 6.617000 }
  @{ name = "Val d'Allos - La Foux"; slug = "val-dallos-la-foux"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-de-Haute-Provence"; latitude = 44.310000; longitude = 6.625000 }
  @{ name = "Isola 2000"; slug = "isola-2000"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-Maritimes"; latitude = 44.188000; longitude = 7.158000 }
  @{ name = "Auron"; slug = "auron"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-Maritimes"; latitude = 44.255000; longitude = 6.943000 }
  @{ name = "Valberg"; slug = "valberg"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-Maritimes"; latitude = 44.100000; longitude = 6.983000 }
  @{ name = "Gréolières-les-Neiges"; slug = "greolieres-les-neiges"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-Maritimes"; latitude = 43.800000; longitude = 6.950000 }
  @{ name = "La Mongie (Grand Tourmalet)"; slug = "la-mongie-grand-tourmalet"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.907000; longitude = 0.178000 }
  @{ name = "Luz Ardiden"; slug = "luz-ardiden"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.919000; longitude = -0.017000 }
  @{ name = "Cauterets"; slug = "cauterets"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.892000; longitude = -0.116000 }
  @{ name = "Piau-Engaly"; slug = "piau-engaly"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.748000; longitude = 0.152000 }
  @{ name = "Hautacam"; slug = "hautacam"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.987000; longitude = -0.046000 }
  @{ name = "Gourette"; slug = "gourette"; region = "Nouvelle-Aquitaine"; department = "Pyrénées-Atlantiques"; latitude = 42.973000; longitude = -0.334000 }
  @{ name = "La Pierre Saint-Martin"; slug = "la-pierre-saint-martin"; region = "Nouvelle-Aquitaine"; department = "Pyrénées-Atlantiques"; latitude = 42.981000; longitude = -0.744000 }
  @{ name = "Ax 3 Domaines"; slug = "ax-3-domaines"; region = "Occitanie"; department = "Ariège"; latitude = 42.720000; longitude = 1.812000 }
  @{ name = "Guzet"; slug = "guzet"; region = "Occitanie"; department = "Ariège"; latitude = 42.808000; longitude = 1.326000 }
  @{ name = "Le Mourtis"; slug = "le-mourtis"; region = "Occitanie"; department = "Haute-Garonne"; latitude = 42.954000; longitude = 0.690000 }
  @{ name = "Luchon-Superbagnères"; slug = "luchon-superbagneres"; region = "Occitanie"; department = "Haute-Garonne"; latitude = 42.793000; longitude = 0.558000 }
  @{ name = "Peyragudes"; slug = "peyragudes"; region = "Occitanie"; department = "Haute-Garonne"; latitude = 42.784000; longitude = 0.459000 }
  @{ name = "Font-Romeu / Pyrénées 2000"; slug = "font-romeu-pyrenees-2000"; region = "Occitanie"; department = "Pyrénées-Orientales"; latitude = 42.505000; longitude = 2.035000 }
  @{ name = "Les Angles"; slug = "les-angles"; region = "Occitanie"; department = "Pyrénées-Orientales"; latitude = 42.576000; longitude = 2.074000 }
  @{ name = "Porté-Puymorens"; slug = "porte-puymorens"; region = "Occitanie"; department = "Pyrénées-Orientales"; latitude = 42.543000; longitude = 1.889000 }
  @{ name = "Formiguères"; slug = "formigueres"; region = "Occitanie"; department = "Pyrénées-Orientales"; latitude = 42.602000; longitude = 2.126000 }
  @{ name = "Super-Besse"; slug = "super-besse"; region = "Auvergne-Rhône-Alpes"; department = "Puy-de-Dôme"; latitude = 45.514000; longitude = 2.855000 }
  @{ name = "Le Mont-Dore"; slug = "le-mont-dore"; region = "Auvergne-Rhône-Alpes"; department = "Puy-de-Dôme"; latitude = 45.589000; longitude = 2.808000 }
  @{ name = "Le Lioran"; slug = "le-lioran"; region = "Auvergne-Rhône-Alpes"; department = "Cantal"; latitude = 45.052000; longitude = 2.755000 }
  @{ name = "Métabief"; slug = "metabief"; region = "Bourgogne-Franche-Comté"; department = "Doubs"; latitude = 46.773000; longitude = 6.354000 }
  @{ name = "Les Rousses"; slug = "les-rousses"; region = "Bourgogne-Franche-Comté"; department = "Jura"; latitude = 46.484000; longitude = 6.060000 }
  @{ name = "Monts Jura"; slug = "monts-jura"; region = "Auvergne-Rhône-Alpes"; department = "Ain"; latitude = 46.258000; longitude = 5.933000 }
  @{ name = "La Bresse-Hohneck"; slug = "la-bresse-hohneck"; region = "Grand Est"; department = "Vosges"; latitude = 48.031000; longitude = 6.955000 }
  @{ name = "Gérardmer"; slug = "gerardmer"; region = "Grand Est"; department = "Vosges"; latitude = 48.070000; longitude = 6.879000 }
  @{ name = "Le Lac Blanc"; slug = "le-lac-blanc"; region = "Grand Est"; department = "Haut-Rhin"; latitude = 48.137000; longitude = 7.132000 }
  @{ name = "Ghisoni-Capanelle"; slug = "ghisoni-capanelle"; region = "Corse"; department = "Haute-Corse"; latitude = 42.102000; longitude = 9.238000 }
  @{ name = "Asco (Haut-Asco)"; slug = "asco-haut-asco"; region = "Corse"; department = "Haute-Corse"; latitude = 42.411000; longitude = 8.953000 }
  @{ name = "Hirmentaz - Bellevaux"; slug = "hirmentaz-bellevaux"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.276000; longitude = 6.510000 }
  @{ name = "Habère-Poche"; slug = "habere-poche"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.273000; longitude = 6.465000 }
  @{ name = "Bernex Dent d'Oche"; slug = "bernex-dent-doche"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.358000; longitude = 6.693000 }
  @{ name = "Thollon-les-Mémises"; slug = "thollon-les-memises"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.386000; longitude = 6.654000 }
  @{ name = "Mont-Saxonnex"; slug = "mont-saxonnex"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 46.060000; longitude = 6.475000 }
  @{ name = "Plaine-Joux (Passy)"; slug = "plaine-joux-passy"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.956000; longitude = 6.733000 }
  @{ name = "Praz-sur-Arly"; slug = "praz-sur-arly"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Savoie"; latitude = 45.821000; longitude = 6.572000 }
  @{ name = "Arêches-Beaufort"; slug = "areches-beaufort"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.707000; longitude = 6.571000 }
  @{ name = "Notre-Dame-de-Bellecombe"; slug = "notre-dame-de-bellecombe"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.807000; longitude = 6.519000 }
  @{ name = "Crest-Voland / Cohennoz"; slug = "crest-voland-cohennoz"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.781000; longitude = 6.503000 }
  @{ name = "Valmeinier"; slug = "valmeinier"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.180000; longitude = 6.483000 }
  @{ name = "Valfréjus"; slug = "valfrejus"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.159000; longitude = 6.671000 }
  @{ name = "Orelle"; slug = "orelle"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.219000; longitude = 6.535000 }
  @{ name = "Bonneval-sur-Arc"; slug = "bonneval-sur-arc"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.369000; longitude = 7.044000 }
  @{ name = "Albiez-Montrond"; slug = "albiez-montrond"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.227000; longitude = 6.365000 }
  @{ name = "La Toussuire"; slug = "la-toussuire"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.242000; longitude = 6.290000 }
  @{ name = "Saint-Jean-d'Arves"; slug = "saint-jean-darves"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.209000; longitude = 6.286000 }
  @{ name = "Saint-Colomban-des-Villards"; slug = "saint-colomban-des-villards"; region = "Auvergne-Rhône-Alpes"; department = "Savoie"; latitude = 45.297000; longitude = 6.267000 }
  @{ name = "Alpe du Grand Serre"; slug = "alpe-du-grand-serre"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.017000; longitude = 5.819000 }
  @{ name = "Gresse-en-Vercors"; slug = "gresse-en-vercors"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 44.918000; longitude = 5.548000 }
  @{ name = "Le Collet d'Allevard"; slug = "le-collet-dallevard"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.412000; longitude = 6.104000 }
  @{ name = "Col de Porte (Chartreuse)"; slug = "col-de-porte-chartreuse"; region = "Auvergne-Rhône-Alpes"; department = "Isère"; latitude = 45.311000; longitude = 5.769000 }
  @{ name = "Col de Rousset"; slug = "col-de-rousset"; region = "Auvergne-Rhône-Alpes"; department = "Drôme"; latitude = 44.842000; longitude = 5.405000 }
  @{ name = "Lélex - Crozet (Monts Jura)"; slug = "lelex-crozet-monts-jura"; region = "Auvergne-Rhône-Alpes"; department = "Ain"; latitude = 46.340000; longitude = 5.955000 }
  @{ name = "Mijoux - La Faucille (Monts Jura)"; slug = "mijoux-la-faucille-monts-jura"; region = "Auvergne-Rhône-Alpes"; department = "Ain"; latitude = 46.358000; longitude = 6.008000 }
  @{ name = "Les Orres"; slug = "les-orres"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.490000; longitude = 6.555000 }
  @{ name = "Ceillac"; slug = "ceillac"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.659000; longitude = 6.747000 }
  @{ name = "Réallon"; slug = "reallon"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.566000; longitude = 6.408000 }
  @{ name = "Saint-Léger-les-Mélèzes"; slug = "saint-leger-les-melezes"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.627000; longitude = 6.238000 }
  @{ name = "Ancelle"; slug = "ancelle"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.643000; longitude = 6.180000 }
  @{ name = "Crévoux"; slug = "crevoux"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.534000; longitude = 6.607000 }
  @{ name = "SuperDévoluy"; slug = "superdevoluy"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.682000; longitude = 5.861000 }
  @{ name = "La Joue du Loup"; slug = "la-joue-du-loup"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.695000; longitude = 5.844000 }
  @{ name = "Abriès - Ristolas"; slug = "abries-ristolas"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.781000; longitude = 6.973000 }
  @{ name = "Molines-en-Queyras"; slug = "molines-en-queyras"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.710000; longitude = 6.882000 }
  @{ name = "Saint-Véran"; slug = "saint-veran"; region = "Provence-Alpes-Côte d'Azur"; department = "Hautes-Alpes"; latitude = 44.700000; longitude = 7.103000 }
  @{ name = "Saint-Jean Montclar"; slug = "saint-jean-montclar"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-de-Haute-Provence"; latitude = 44.444000; longitude = 6.330000 }
  @{ name = "Chabanon - Selonnet"; slug = "chabanon-selonnet"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-de-Haute-Provence"; latitude = 44.308000; longitude = 6.253000 }
  @{ name = "Le Grand Puy"; slug = "le-grand-puy"; region = "Provence-Alpes-Côte d'Azur"; department = "Alpes-de-Haute-Provence"; latitude = 44.266000; longitude = 6.427000 }
  @{ name = "Saint-Lary-Soulan"; slug = "saint-lary-soulan"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.829000; longitude = 0.324000 }
  @{ name = "Gavarnie - Gèdre"; slug = "gavarnie-gedre"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.731000; longitude = -0.014000 }
  @{ name = "Val Louron"; slug = "val-louron"; region = "Occitanie"; department = "Hautes-Pyrénées"; latitude = 42.796000; longitude = 0.412000 }
  @{ name = "Bourg d'Oueil"; slug = "bourg-doueil"; region = "Occitanie"; department = "Haute-Garonne"; latitude = 42.877000; longitude = 0.575000 }
  @{ name = "Ascou - Pailhères"; slug = "ascou-pailheres"; region = "Occitanie"; department = "Ariège"; latitude = 42.724000; longitude = 1.900000 }
  @{ name = "Mijanès - Donezan"; slug = "mijanes-donezan"; region = "Occitanie"; department = "Ariège"; latitude = 42.733000; longitude = 2.057000 }
  @{ name = "Monts d'Olmes"; slug = "monts-dolmes"; region = "Occitanie"; department = "Ariège"; latitude = 42.874000; longitude = 1.730000 }
  @{ name = "Artouste"; slug = "artouste"; region = "Nouvelle-Aquitaine"; department = "Pyrénées-Atlantiques"; latitude = 42.882000; longitude = -0.293000 }
  @{ name = "Cambre d'Aze"; slug = "cambre-daze"; region = "Occitanie"; department = "Pyrénées-Orientales"; latitude = 42.492000; longitude = 2.118000 }
  @{ name = "La Quillane"; slug = "la-quillane"; region = "Occitanie"; department = "Pyrénées-Orientales"; latitude = 42.575000; longitude = 2.161000 }
  @{ name = "Chastreix - Sancy"; slug = "chastreix-sancy"; region = "Auvergne-Rhône-Alpes"; department = "Puy-de-Dôme"; latitude = 45.508000; longitude = 2.783000 }
  @{ name = "Les Estables - Mézenc"; slug = "les-estables-mezenc"; region = "Auvergne-Rhône-Alpes"; department = "Haute-Loire"; latitude = 44.933000; longitude = 4.229000 }
  @{ name = "Croix de Bauzon"; slug = "croix-de-bauzon"; region = "Auvergne-Rhône-Alpes"; department = "Ardèche"; latitude = 44.612000; longitude = 4.145000 }
  @{ name = "Le Bleymard - Mont Lozère"; slug = "le-bleymard-mont-lozere"; region = "Occitanie"; department = "Lozère"; latitude = 44.457000; longitude = 3.734000 }
  @{ name = "Laguiole (Le Bouyssou)"; slug = "laguiole-le-bouyssou"; region = "Occitanie"; department = "Aveyron"; latitude = 44.683000; longitude = 2.842000 }
  @{ name = "Brameloup"; slug = "brameloup"; region = "Occitanie"; department = "Aveyron"; latitude = 44.388000; longitude = 2.814000 }
  @{ name = "Les Rousses - La Dôle secteur alpin"; slug = "les-rousses-la-dole-secteur-alpin"; region = "Bourgogne-Franche-Comté"; department = "Jura"; latitude = 46.451000; longitude = 6.071000 }
  @{ name = "Le Markstein"; slug = "le-markstein"; region = "Grand Est"; department = "Haut-Rhin"; latitude = 47.920000; longitude = 7.028000 }
  @{ name = "Schnepfenried"; slug = "schnepfenried"; region = "Grand Est"; department = "Haut-Rhin"; latitude = 47.986000; longitude = 7.062000 }
  @{ name = "Ventron (Frère Joseph)"; slug = "ventron-frere-joseph"; region = "Grand Est"; department = "Vosges"; latitude = 47.944000; longitude = 6.863000 }
  @{ name = "La Bresse Lispach"; slug = "la-bresse-lispach"; region = "Grand Est"; department = "Vosges"; latitude = 48.039000; longitude = 6.946000 }
)

foreach ($s in $stations) {
  try {
    Write-Host "▶ Station:" $s.name "("+ $s.slug +")" -ForegroundColor Cyan
    # existence check
    $exists = $false
    try {
      $r0 = Invoke-RestMethod -Method GET -Uri ($api + $s.slug)
      if ($r0 -and $r0.resort) { $exists = $true }
    } catch { $exists = $false }

    if (-not $exists) {
      $body = @{
        name = $s.name
        slug = $s.slug
        latitude = if ($s.latitude -ne $null) { [double]$s.latitude } else { $null }
        longitude = if ($s.longitude -ne $null) { [double]$s.longitude } else { $null }
        region = if ($s.region) { $s.region } else { $null }
        department = if ($s.department) { $s.department } else { $null }
      } | ConvertTo-Json
      $resp = Invoke-RestMethod -Method POST -Uri $api -Headers @{ "Content-Type"="application/json" } -Body $body
      Write-Host "  + created" -ForegroundColor Green
    } else {
      $patch = @{ }
      if ($s.latitude -ne $null)  { $patch.latitude  = [double]$s.latitude }
      if ($s.longitude -ne $null) { $patch.longitude = [double]$s.longitude }
      if ($s.region)     { $patch.region     = $s.region }
      if ($s.department) { $patch.department = $s.department }
      if ($patch.Keys.Count -gt 0) {
        $json = $patch | ConvertTo-Json
        $resp = Invoke-RestMethod -Method PATCH -Uri ($api + $s.slug) -Headers @{ "Content-Type"="application/json" } -Body $json
        Write-Host "  ~ updated (lat/lon/region/department)" -ForegroundColor Yellow
      } else {
        Write-Host "  = no changes" -ForegroundColor DarkGray
      }
    }
  } catch {
    Write-Warning ("  ! error: " + $_.Exception.Message)
  }
}