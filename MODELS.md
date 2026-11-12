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

### Compteur — VitTrack (OpenCV Zoo)

| Nom dans moto-blur | Taille | Usage |
|---|---|---|
| `vittrack` | 0,7 Mo | suivi du compteur à partir d'un rectangle tracé sur une image |

- Source : <https://huggingface.co/opencv/object_tracking_vittrack>
  (révision `868e4c941984c6363594ec34ee1c3ba0b75378a3`, fichier `object_tracking_vittrack_2023sep.onnx`).
- Licence : Apache-2.0 (fichier LICENSE du dépôt).
- Exécuté par le module de réseaux de neurones d'OpenCV, sur le processeur.

```
vittrack  2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d
```

### Contour précis — SAM 2.1 « tiny » (Meta, conversion ONNX `onnx-community`)

| Nom dans moto-blur | Taille | Usage |
|---|---|---|
| `sam2.1-tiny` | 155 Mo (4 fichiers) | silhouette exacte d'un objet suivi, à la demande (« Contour précis ») |

- Source : <https://huggingface.co/onnx-community/sam2.1-hiera-tiny-ONNX>
  (révision `814a066640debee5a91e70aa401fb8e17e030503`, dossier `onnx/`, version non quantifiée).
- Modèle d'origine : SAM 2.1 de Meta, <https://github.com/facebookresearch/sam2>, licence Apache-2.0.
  Le dépôt de la conversion ne porte pas de fichier de licence propre : **à confirmer**, la
  conversion d'un modèle Apache-2.0 restant en principe sous cette licence.
- Téléchargé seulement si vous demandez un contour précis. Utilisé image par image : la boîte
  donnée par le suivi (VitTrack) sert d'indication, le modèle renvoie la silhouette. Le mode
  « vidéo » de SAM 2 (mémoire entre images) n'est pas utilisé.
- Exécution : processeur ; sous Windows, DirectML est essayé d'abord, avec repli automatique sur
  le processeur. **Non vérifié sur une Radeon ni sur un Mac** : seule l'exécution sur processeur
  (Linux) a été mesurée, à environ 1,2 s par silhouette sur une machine lente.

```
vision_encoder.onnx                    4f30aacd3aaefbca81a0b7fe4c1fc96345570ea0a6f80ced599493d1b3be2e8c
vision_encoder.onnx_data               e83df9866a5afe68ea7f0f721f18f65137fc3acbf0da1c74e946d363e09c69cc
prompt_encoder_mask_decoder.onnx       874414704c5d686db7d206a35f6e15d26563d50c8c4468fccc6739bd7e491dcf
prompt_encoder_mask_decoder.onnx_data  e9874d900dd4134ed60eab1e97910327c2419e0b2954485d8fd6e7f1a1470f47
```

### Modèles personnels

Un modèle entraîné par vous (voir `docs/ENTRAINEMENT.md`) se pose dans
`~/.moto-blur/models/perso/plate/` (ou `perso/face/`) : fichier ONNX exporté par Ultralytics,
une seule classe. Il apparaît alors dans la liste des modèles. moto-blur ne le télécharge ni ne
l'envoie nulle part. Sa licence dépend de l'outil d'entraînement : un modèle entraîné avec
Ultralytics est sous AGPL-3.0, comme moto-blur.

### Conversion locale des modèles de plaques

Les modèles de plaques sont publiés avec la suppression des doublons intégrée et une seule image
par appel. Au premier usage, moto-blur en tire sur votre machine une version « par lot »
(`<nom>.lot.onnx`, à côté du fichier téléchargé) qui traite toutes les tuiles d'une image en un
appel ; la suppression des doublons est alors faite par moto-blur, comme pour les visages. Les
poids sont inchangés. Si la conversion échoue, le fichier d'origine est utilisé tel quel.

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
| SAM 2.1 en mode vidéo (Apache-2.0) | Pipeline en cinq graphes ONNX jamais annoncé testé sur CoreML ou DirectML. Non intégré : le suivi reste fait par VitTrack, et SAM 2.1 n'est utilisé qu'image par image pour le contour précis (voir plus haut). |
| SAM 3 / 3.1 (pour le compteur) | Licence « SAM License » avec restrictions d'usage, et suivi vidéo limité à CUDA. |

## Bibliothèques

| Bibliothèque | Licence | Remarque |
|---|---|---|
| ONNX Runtime | MIT | CoreML sur macOS, DirectML sur Windows (`onnxruntime-directml` 1.24.4, dernière version publiée de ce paquet) |
| PyAV | BSD-3-Clause | ses paquets embarquent FFmpeg avec x264 et x265 (GPL), compatibles avec l'AGPL-3.0 |
| OpenCV | Apache-2.0 | |
| NumPy | BSD-3-Clause | |
| ONNX | Apache-2.0 | conversion locale des modèles de plaques, lecture de l'en-tête des modèles personnels |
