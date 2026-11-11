# Modèles utilisés et licences

moto-blur ne contient aucun modèle dans son dépôt. Les modèles sont téléchargés au premier
usage dans `~/.moto-blur/models` (ou le dossier indiqué par la variable `MOTO_BLUR_MODELS`),
depuis les adresses ci-dessous, et leur empreinte SHA-256 est vérifiée. C'est le seul appel
réseau du logiciel.

État des vérifications : novembre 2026. Ce document n'est pas un avis juridique.

## Modèles disponibles

### Visages — YOLO11-face (dépôt Hugging Face `deepghs/yolo-face`)

| Nom dans moto-blur | Taille | Entrée | Usage |
|---|---|---|---|
| `yolo11n-face` | 10 Mo | 640 px | le plus rapide, moins bon sur les petits visages |
| `yolo11s-face` (défaut) | 36 Mo | 640 px | compromis |
| `yolo11m-face` | 77 Mo | 640 px | le plus précis, le plus lent |

- Source : <https://huggingface.co/deepghs/yolo-face> (révision `e3662574830c534dfcc9c3b7ea4d89272f8aae4e`),
  conversions ONNX des poids de <https://github.com/akanametov/yolo-face>.
- Licence du code d'entraînement : GPL-3.0 (dépôt akanametov), sur une base Ultralytics en AGPL-3.0.
- Licence des poids : les fichiers ONNX déclarent eux-mêmes, dans leurs métadonnées,
  « AGPL-3.0 License (https://ultralytics.com/license) ». Le dépôt Hugging Face est étiqueté
  « other », sans texte de licence.
- Données d'entraînement : WIDER FACE (indiqué dans les métadonnées des fichiers).
- Compatibilité : AGPL-3.0, identique à la licence de moto-blur.
- **Zone grise** : WIDER FACE est décrit par plusieurs sources tierces comme réservé à la
  recherche non commerciale ; le site du jeu de données était inaccessible lors de la
  vérification. La question de savoir si les conditions d'un jeu de données s'imposent aux poids
  entraînés dessus n'est pas tranchée juridiquement. Tous les détecteurs de visages performants
  et ouverts sont dans ce cas.

Empreintes SHA-256 :

```
yolo11n-face  7b4b3a098ac15a5ccd0d048e7949b63275e4a9657c374ad9e4730632d9f01829
yolo11s-face  de2d0582451a323af9a0fb05da70f32b4f26fcf411657c2dd491037ecd18efe6
yolo11m-face  0136c7938193241ebde248abc0678455c01120976dacea425af53d72fc4a70a3
```

### Plaques — YOLOv9 (projet `open-image-models`)

| Nom dans moto-blur | Taille | Entrée | Rappel annoncé par l'auteur |
|---|---|---|---|
| `yolo-v9-t-640` | 7,5 Mo | 640 px | 0,896 |
| `yolo-v9-s-608` (défaut) | 27 Mo | 608 px | 0,917 |

- Source : <https://github.com/ankandrew/open-image-models> (fichiers de la publication `assets`).
- Licence du dépôt : MIT.
- Licence des poids : **aucune mention distincte**. Un projet tiers (OpenScrub) les décrit comme
  MIT. À confirmer auprès de l'auteur.
- Données d'entraînement : **non documentées** ; présence de plaques européennes non confirmée.
- Le code d'entraînement YOLOv9 d'origine est, de mémoire, sous GPL-3.0 (non revérifié) ;
  compatible avec l'AGPL dans tous les cas.
- Les rappels annoncés sont mesurés sur le jeu de test de l'auteur, pas sur des vidéos
  d'action cam. À mesurer sur vos propres vidéos.
- Particularité technique : la suppression des doublons est intégrée au modèle, avec un seuil de
  score de 0,001 (vérifié dans le graphe) ; elle ne bride donc pas les seuils bas de moto-blur.

Empreintes SHA-256 :

```
yolo-v9-t-640  c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076
yolo-v9-s-608  2b878b38d9aa07b6ddc3ea75c4ffcb39869bc5c218e0a14002f60ab2f7b0be9a
```

## Modèles étudiés et écartés

| Modèle | Raison |
|---|---|
| SCRFD / RetinaFace (InsightFace) | Poids explicitement réservés à la recherche non commerciale. |
| YuNet (OpenCV Zoo, MIT) | Licence irréprochable mais nettement plus faible sur les petits visages (WIDER FACE « hard » 75,0). Candidat de repli, non intégré en phase 1. |
| YOLOv5-face (GPL-3.0) | Meilleur score publié (« hard » 85,3 pour la variante m) mais pas de fichier ONNX officiel. À comparer en phase ultérieure si YOLO11-face ne suffit pas. |
| MediaPipe BlazeFace (Apache-2.0) | Conçu pour les visages proches, inadapté aux piétons lointains. |
| YOLOv11 plaques, `morsetechlab` (AGPL-3.0) | Utilisable ; la fiche du modèle reconnaît une contamination entre données d'entraînement et de test. Non intégré en phase 1. |
| RF-DETR plaques, `autolane` (Apache-2.0) | Aucune métrique publiée, provenance des données inconnue. Non intégré en phase 1. |
| EgoBlur Gen2 (Meta, Apache-2.0) | Environ 1,5 s par image sur processeur, format TorchScript sans export ONNX, téléchargement derrière un formulaire. Peut servir à étiqueter des vidéos pour un réentraînement. |
| SAM 3 / 3.1 (pour le compteur, phase 2) | Licence « SAM License » avec restrictions d'usage, et suivi vidéo limité à CUDA. |

## Bibliothèques

| Bibliothèque | Licence | Remarque |
|---|---|---|
| ONNX Runtime | MIT | CoreML sur macOS, DirectML sur Windows (`onnxruntime-directml` 1.24.4, dernière version publiée de ce paquet) |
| PyAV | BSD-3-Clause | ses paquets embarquent FFmpeg avec x264 et x265 (GPL), compatibles avec l'AGPL-3.0 |
| OpenCV | Apache-2.0 | |
| NumPy | BSD-3-Clause | |
