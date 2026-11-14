# Entraîner un modèle de plaques sur vos propres vidéos

Le modèle de plaques fourni a été entraîné par un tiers sur des images que nous ne connaissons
pas (voir `MODELS.md`). Il n'a jamais vu vos trajets, votre caméra, ni l'angle sous lequel elle
voit les plaques. Un modèle affiné sur vos propres images peut mieux faire : moins de plaques
manquées, moins de fausses détections.

**Ce que Flouto fournit** : une commande qui exporte les images de vos projets vérifiés, lance
l'entraînement, convertit le résultat et l'installe ; et une autre qui mesure si le modèle
obtenu fait mieux que le modèle fourni.
**Ce qui n'est pas dans Flouto** : l'outil d'entraînement lui-même (Ultralytics, licence
AGPL-3.0). La commande le télécharge à part, la première fois.

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

## 2. Entraîner, en une commande

Sur le Mac, depuis le dossier de Flouto, en donnant vos projets vérifiés :

```bash
uv run flouto train samples/sortie1.flouto.json samples/sortie2.flouto.json --name mes-plaques
```

La commande enchaîne quatre étapes :

1. elle exporte les images d'entraînement (dossier `entrainement_mes-plaques`, à côté du premier
   projet) : `images/`, `labels/`, la liste d'entraînement et celle gardée pour la mesure ;
2. elle lance l'entraînement avec Ultralytics, sur la puce Apple si elle est là ;
3. elle convertit le résultat au format lu par Flouto ;
4. elle l'installe comme modèle personnel dans `~/.flouto/models/perso/plate/`.

Ce qu'il faut savoir :

- **La première fois, l'outil d'entraînement est téléchargé** (plusieurs centaines de Mo), puis
  le modèle de départ. C'est le seul moment où cette commande utilise le réseau. L'analyse et
  le rendu de Flouto, eux, n'en font toujours aucun usage.
- **Les images exportées montrent des plaques non floutées.** Elles restent sur votre machine ;
  ne les publiez pas (le dossier `samples/` est déjà ignoré par git).
- Avec moins de 50 plaques vérifiées, la commande refuse : ce serait du temps perdu.
- Options : `--epochs 80` (nombre de passes), `--base yolo11n|yolo11s|yolo11m` (plus rapide ou
  plus précis), `--device cpu` pour forcer le processeur.
- Sur le PC Windows, PyTorch ne sait pas utiliser simplement une Radeon : l'entraînement se
  ferait sur le processeur, beaucoup plus lentement. Le Mac est le bon choix.
- Les mesures affichées à la fin de l'entraînement sont flatteuses : les images de mesure
  viennent des mêmes sorties que les images d'entraînement. Le vrai test est à l'étape 3.

La chaîne complète a été essayée de bout en bout (export, entraînement de deux passes,
conversion, installation, chargement dans Flouto) sur une machine Linux sans carte
graphique. Elle n'a pas été essayée sur un Mac, et aucun modèle utile n'a été entraîné : il
faut vos projets pour cela.

Pour seulement exporter les images, sans entraîner : `uv run flouto dataset PROJET -o DOSSIER`,
ou le bouton « Exporter des images d'entraînement » des réglages experts.

## 3. Vérifier qu'il fait mieux

Sur un projet vérifié dont la vidéo n'a **pas** servi à l'entraînement :

```bash
uv run flouto measure samples/autre-sortie.flouto.json --plate-model mes-plaques
```

La commande réanalyse la vidéo avec votre modèle, sans toucher au projet, et compare :

- les flous que vous aviez gardés et qui seraient **perdus** ;
- les objets que vous aviez dû ajouter à la main et que le modèle **trouve seul** ;
- les faux flous que vous aviez retirés et qui **reviennent** ;
- la durée de l'analyse.

Ne gardez votre modèle que s'il ne perd aucun flou et fait revenir moins de faux flous. En cas
de doute, gardez le modèle fourni : mieux vaut un flou de trop qu'une plaque visible.

Pour l'utiliser ensuite : mode avancé, réglages experts, « Modèle de plaques ».

## Licence

Un modèle entraîné avec Ultralytics est soumis à l'AGPL-3.0, la licence de Flouto. Tant
qu'il reste sur votre machine, cela n'a pas de conséquence pratique.
