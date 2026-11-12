# Entraîner un modèle de plaques sur vos propres vidéos

Le modèle de plaques fourni a été entraîné par un tiers sur des images que nous ne connaissons
pas (voir `MODELS.md`). Il n'a jamais vu vos trajets, votre caméra, ni l'angle sous lequel elle
voit les plaques. Un modèle affiné sur vos propres images peut mieux faire : moins de plaques
manquées, moins de fausses détections.

**Ce que moto-blur fournit** : l'export des images d'entraînement à partir de projets que vous
avez vérifiés, et la possibilité d'utiliser un modèle que vous avez entraîné.
**Ce que moto-blur ne fait pas** : l'entraînement lui-même. Il demande un outil séparé
(Ultralytics, licence AGPL-3.0), installé à part, et un peu de temps de calcul.

Rien de ce qui suit n'a pu être essayé sur vos machines ni sur vos vidéos : prenez-le comme une
marche à suivre raisonnable, pas comme une promesse de résultat.

## 1. Préparer des projets vérifiés

La qualité du modèle dépend entièrement de la qualité de vos corrections :

- les plaques **gardées** (floutées) deviennent des exemples de plaques ;
- les détections que vous avez **retirées** (« ce n'est pas à flouter ») deviennent des
  contre-exemples : le modèle apprend à ne plus s'y tromper ;
- les détections écartées par leur score que vous n'avez pas examinées, et les flous ajoutés à
  la main, sont laissés de côté (on ne sait pas ce qu'ils montrent).

Une plaque visible que personne n'a floutée est le pire cas : le modèle apprendrait que ce
n'est pas une plaque. Avant d'exporter, faites donc la relecture complète et ajoutez ce qui
manque.

Ordre de grandeur, à titre indicatif : quelques centaines d'images contenant des plaques,
prises sur plusieurs sorties différentes (jour, soir, ville, route), sont un minimum pour
espérer un gain. Une seule vidéo ne suffit pas.

## 2. Exporter les images

Dans l'interface, mode avancé, réglages d'analyse : « Exporter des images d'entraînement ».
Ou en ligne de commande, pour chaque projet vérifié :

```bash
uv run moto-blur dataset samples/sortie1.motoblur.json -o entrainement
uv run moto-blur dataset samples/sortie2.motoblur.json -o entrainement
```

Le même dossier de sortie peut recevoir plusieurs projets : les images s'ajoutent. Le dossier
contient `images/`, `labels/`, `train.txt` (images d'entraînement), `val.txt` (une image sur
cinq, gardée pour mesurer le modèle) et `data.yaml`.

**Ces images montrent des plaques non floutées.** Elles restent sur votre machine ; ne les
publiez pas et ne les mettez pas dans le dépôt (le dossier `samples/` est déjà ignoré par git).

## 3. Entraîner (hors moto-blur)

Sur le Mac (puce Apple, accélération `mps`) :

```bash
uvx --from ultralytics yolo detect train data=entrainement/data.yaml model=yolo11s.pt imgsz=640 epochs=80 device=mps
```

Sur le PC Windows, PyTorch ne sait pas utiliser une Radeon simplement : l'entraînement se
ferait sur le processeur (`device=cpu`), beaucoup plus lentement. Le Mac est le meilleur choix.

Utilisez bien un modèle `yolo11` (`yolo11n.pt` plus rapide, `yolo11s.pt` plus précis) : moto-blur
lit la sortie de cette famille. Les modèles dont la suppression des doublons est intégrée
(YOLOv10, YOLO26) ont une sortie différente, que moto-blur ne lit pas pour un modèle personnel.

La première commande télécharge le modèle de départ. À la fin, Ultralytics indique le dossier
du résultat (`runs/detect/train/weights/best.pt`) et des mesures. Ces mesures sont flatteuses :
les images de mesure viennent des mêmes sorties que les images d'entraînement. Le vrai test est
à l'étape 5.

## 4. Convertir et installer

```bash
uvx --from ultralytics yolo export model=runs/detect/train/weights/best.pt format=onnx dynamic=True
```

Copiez le fichier `best.onnx` obtenu sous le nom de votre choix dans :

- macOS : `~/.moto-blur/models/perso/plate/mes-plaques.onnx`
- Windows : `C:\Users\<vous>\.moto-blur\models\perso\plate\mes-plaques.onnx`

Le modèle apparaît alors dans la liste « Modèle de plaques » des réglages d'analyse (mode
avancé), ou s'utilise avec `--plate-model mes-plaques` en ligne de commande.

## 5. Vérifier qu'il fait mieux

Sur une vidéo qui n'a **pas** servi à l'entraînement :

1. analysez-la avec le modèle fourni, notez le nombre de plaques manquées et de fausses
   détections trouvées à la vérification ;
2. changez de modèle, relancez l'analyse, refaites la vérification.

Ne gardez votre modèle que s'il manque moins de plaques. L'exigence reste la même : mieux vaut
un flou de trop qu'une plaque visible. En cas de doute, gardez le modèle fourni.

## Licence

Un modèle entraîné avec Ultralytics est soumis à l'AGPL-3.0, la licence de moto-blur. Tant
qu'il reste sur votre machine, cela n'a pas de conséquence pratique.
