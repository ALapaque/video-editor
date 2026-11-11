# Choix techniques et recommandations

## Compteur : les trois modes

| Mode | État | Quand l'utiliser |
|---|---|---|
| 1. Zone fixe | implémenté | caméra fixée à la moto, cadrage constant : le compteur ne bouge pas dans l'image |
| 2. Suivi à partir d'un clic | implémenté avec VitTrack | cadrage recalculé depuis le 360 : le compteur se déplace dans l'image |
| 3. Détecteur entraîné | non fait | seulement si le mode 2 décroche trop souvent sur vos vidéos |

**Recommandation : le mode 2, avec VitTrack, comme mode par défaut.**

- VitTrack (OpenCV, Apache-2.0, 0,7 Mo) tourne sur le processeur sans accélération : environ
  140 images par seconde mesurées ici sur une vidéo 1080p de synthèse, décodage compris. Il
  renvoie à chaque image un rectangle et un score ; quand le score chute, moto-blur maintient
  la dernière position sûre et signale le passage.
- Limite : il rend un rectangle, pas un contour. Pour un compteur, objet grand et à peu près
  rectangulaire, c'est suffisant. Il n'a été essayé que sur des vidéos de synthèse ; sa tenue
  sur de vraies séquences de plusieurs minutes (vibrations, soleil sur l'écran, compteur qui
  sort du cadre) reste à mesurer sur vos vidéos.
- Le modèle de segmentation vidéo envisagé au départ, SAM 2.1 (Apache-2.0), n'est **pas
  intégré**. Il donnerait un contour exact, mais son pipeline vidéo se compose de cinq graphes
  ONNX qu'aucun export public n'annonce testés sur CoreML ou DirectML, et il est bien plus
  lourd. Il ne vaut l'effort que si VitTrack s'avère insuffisant. SAM 3 est écarté (licence à
  restrictions d'usage, suivi vidéo limité à CUDA).
- Le mode 3 demanderait d'annoter des images de votre compteur et d'entraîner un détecteur :
  à n'envisager qu'en dernier.

## Timeline : lecture fluide en 4K

Un navigateur (et la vue web d'une application Tauri) ne lit pas de façon fluide du H.265 4K
10 bits, et pas du tout sur certaines machines. Chaque clip est donc converti une fois en
**fichier proxy** : H.264, 540 lignes, 8 bits, une image clé toutes les demi-secondes, sans
images B. Conséquences :

- La lecture est fluide et le placement sur n'importe quelle image est immédiat.
- Le proxy a exactement les mêmes images aux mêmes instants que la source : le numéro d'image
  affiché est celui de la source, les coupes restent exactes à l'image près.
- Le proxy ne sert qu'à l'affichage. L'analyse et l'export lisent toujours les originaux.
- Pour juger un détail (petite plaque), le mode « Image exacte » décode l'image source en
  pleine résolution et lui applique le flou réel du rendu.
- Coût : une passe de décodage par clip, une seule fois, et environ 30 Mo par minute (mesuré
  sur la vidéo d'essai).

## Application de bureau : comment embarquer le moteur

| | Moteur Python embarqué (retenu) | Réécriture en Rust |
|---|---|---|
| Travail | déjà fait : le moteur est empaqueté tel quel avec PyInstaller | réécrire détection, suivi, zones, rendu, audio, serveur |
| Risque | faible : un seul code, celui qui est testé | élevé : deux implémentations à garder identiques pendant la transition, là où une divergence produit une fuite |
| Taille de l'application | environ 370 Mo mesurés sous Linux (Python, ONNX Runtime, FFmpeg, OpenCV) | de l'ordre de 50 à 100 Mo (estimation) |
| Démarrage | 0,2 s mesuré sous Linux pour lancer le moteur | immédiat |
| Bibliothèques | ONNX Runtime, PyAV, OpenCV : stables | la bibliothèque Rust `ort` est encore en version candidate (2.0.0-rc) ; les liaisons FFmpeg en Rust sont moins confortables que PyAV |
| Évolutions | rapides, en Python | plus lentes |

**Recommandation : garder le moteur Python embarqué.** L'application Tauri n'est qu'une coque :
elle lance le moteur, attend qu'il annonce l'adresse locale de son interface et l'affiche. Le
moteur s'arrête quand la fenêtre se ferme. L'interface est la même qu'avec `moto-blur ui`, ce
qui permet de tout développer et tester dans un navigateur.

Une réécriture ne se justifierait que si la taille de l'application devenait un vrai frein à
la distribution. Elle pourrait alors être partielle : le rendu d'abord, le reste ensuite.

État de la vérification : la coque a été compilée et lancée sous Linux, où elle démarre le
moteur embarqué, affiche l'interface et arrête le moteur à la fermeture. Les installeurs macOS
et Windows sont décrits dans `.github/workflows/installeurs.yml` mais n'ont jamais été
fabriqués ni essayés. Ils ne sont pas signés (la signature demande un compte développeur
Apple payant et un certificat Windows).

## Fonctions hors périmètre, presque gratuites

Proposées, non codées :

- **Fondu d'entrée et de sortie de la vidéo** (image et son) : quelques lignes dans le rendu.
- **Capture d'une image** en pleine résolution depuis l'interface : l'adresse existe déjà
  (c'est l'« image exacte »), il manque un bouton d'enregistrement.
- **Vitesse de lecture réduite dans l'aperçu** (pour vérifier, pas dans l'export) : une ligne.
