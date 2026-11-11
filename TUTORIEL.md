# Tutoriel : démarrer et utiliser moto-blur

Ce guide part de zéro et mène jusqu'à une première vidéo floutée. Comptez 10 minutes
d'installation, puis un premier essai sur un extrait court.

> **Avant de publier une vidéo** : aucun logiciel automatique ne garantit « zéro fuite ». Un
> objet que le détecteur n'a jamais vu n'est ni flouté ni signalé. Regardez toujours le résultat.

## 1. Installer

Il faut deux outils : `git` pour récupérer le projet et `uv` qui installe Python et tout le
reste. Rien d'autre (pas de Node, pas de Rust).

### macOS

Ouvrez l'application **Terminal** et collez ces lignes une par une :

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Fermez puis rouvrez le Terminal, et continuez :

```sh
git clone -b claude/moto-blur-video-blur-5h3zws https://github.com/ALapaque/video-editor.git
cd video-editor
uv sync
```

Si macOS propose d'installer les « outils de ligne de commande » au moment du `git clone`,
acceptez, attendez la fin, puis relancez la commande.

### Windows 10

Ouvrez **PowerShell** (menu Démarrer, tapez « PowerShell ») et collez :

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
winget install --id Git.Git -e
```

Fermez puis rouvrez PowerShell, et continuez :

```powershell
git clone -b claude/moto-blur-video-blur-5h3zws https://github.com/ALapaque/video-editor.git
cd video-editor
uv sync
```

Sans `git`, vous pouvez aussi télécharger le projet en ZIP depuis la page GitHub (bouton
« Code », puis « Download ZIP »), le décompresser, et lancer `uv sync` dans le dossier.

### Vérifier la machine

```sh
uv run moto-blur info
```

Regardez la ligne « ONNX Runtime » :

| Machine | Vous devez lire | Sinon |
|---|---|---|
| Mac | `CoreML` | l'analyse tournera sur le processeur, beaucoup plus lentement |
| Windows | `Dml` | idem ; vérifiez que le pilote de la carte graphique est à jour |

Les encodeurs listés (`hevc_videotoolbox`, `hevc_amf`…) sont ceux que le logiciel essaiera ; il
se replie seul sur un encodeur logiciel si le matériel refuse.

## 2. Lancer l'interface

Créez un dossier `samples` dans `video-editor` et mettez-y une vidéo (ce dossier n'est jamais
envoyé sur GitHub). Puis :

```sh
uv run moto-blur ui
```

Le navigateur s'ouvre sur l'interface. Laissez le terminal ouvert : c'est lui qui fait tourner
le moteur. `Ctrl+C` dans le terminal arrête tout. Utilisez Chrome, Edge ou Safari.

Vous pouvez aussi ouvrir directement une vidéo ou un projet :

```sh
uv run moto-blur ui samples/ma-video.mp4
```

## 3. Premier essai, pas à pas

**Commencez par un extrait de 30 secondes à 1 minute**, pas par une vidéo entière. L'analyse
ne porte que sur les passages gardés : il suffit donc de ne garder qu'un court passage pour
tester vite.

1. **Ouvrir.** Cliquez « Ouvrir une vidéo ou un projet… », naviguez jusqu'à `samples`, cliquez
   la vidéo. Un fichier projet `ma-video.motoblur.json` est créé à côté d'elle. Vos vidéos
   d'origine ne sont jamais modifiées.
2. **Attendre l'aperçu.** Le logiciel fabrique une copie légère de la vidéo pour une lecture
   fluide (barre de progression en haut). Pour une vidéo 4K de 10 minutes, comptez quelques
   minutes ; ce n'est fait qu'une fois par vidéo.
3. **Ne garder qu'un extrait.** Placez la tête de lecture (clic dans la timeline) au début du
   passage voulu, cliquez « ✂ Couper ici ». Placez-la à la fin, « ✂ Couper ici » encore.
   Cliquez les passages à jeter puis « Supprimer le passage ».
4. **Analyser.** Cliquez « Analyser » (bouton orange). Au premier lancement, les modèles de
   détection sont téléchargés (environ 65 Mo). À la fin, un bandeau indique la vitesse en
   images par seconde et si l'accélération matérielle a servi (`CoreML`, `Dml`) ou non (`CPU`).
   **Notez cette ligne**, elle m'intéresse.
5. **Regarder.** Lancez la lecture (barre d'espace). Les zones détectées sont dessinées sur la
   vidéo avec leur score. Dans le menu « Zones », choisissez « aperçu du flou » pour voir à peu
   près le résultat.
6. **Exporter.** Cliquez « Exporter… », gardez les réglages, validez. La vidéo est écrite dans
   le dossier du projet.

## 4. L'interface en détail

### Lecture et déplacement

| Action | Souris | Clavier |
|---|---|---|
| Lecture / pause | bouton « Lecture » | Espace |
| Image précédente / suivante | `◀|` et `|▶` | ← et → |
| Reculer / avancer d'une seconde | | Maj + ← et Maj + → |
| Aller à un instant | clic ou glisser dans la timeline | Début, Fin |

Sous la vidéo, vous lisez l'instant dans le montage, le nom du clip, le numéro d'image et
l'instant dans le fichier source.

### Montage

| Vous voulez | Faites |
|---|---|
| Rogner le début ou la fin | glissez le bord gauche ou droit du passage dans la timeline |
| Supprimer un passage au milieu | « ✂ Couper ici » au début et à la fin, cliquez le passage, « Supprimer le passage » (ou touche Suppr) |
| Récupérer ce qui a été coupé | glissez le bord du passage voisin vers l'extérieur |
| Ajouter un autre clip | « Ajouter un clip… » ; il se place à la fin |
| Changer l'ordre | cliquez un passage, puis « ◀ Déplacer » ou « Déplacer ▶ » |
| Couper le son sur un passage | tête de lecture au début, « 🔇 Muet : début » (ou M) ; tête à la fin, « 🔇 Muet : fin » |
| Retirer un passage muet | panneau « Zones », en bas, « Retirer » |

Chaque modification est enregistrée aussitôt dans le fichier projet. Si vous rallongez un
passage après une analyse, le bouton « Analyser » redevient orange : seule la partie ajoutée
sera analysée.

Si vous assemblez des clips de résolution ou de fréquence d'images différentes, un bandeau vous
demande comment les assembler : sur le format du premier clip, ou sur la plus grande résolution
et la plus haute fréquence. Rien n'est choisi à votre place ; le choix se change ensuite dans
l'onglet « Réglages ». Les clips qui ne sont pas au format retenu sont mis à l'échelle (avec
des bandes noires si le rapport d'image diffère) et leurs images sont dupliquées ou sautées.

### Vérification

L'onglet **« À vérifier »** liste les passages où le logiciel n'est pas sûr de lui : confiance
faible, suivi perdu puis retrouvé, objet très petit, piste très courte, objet au bord de
l'image. Ces passages sont floutés comme les autres ; la liste dit où regarder. Ils sont aussi
marqués en orange sous la timeline.

1. Cliquez un passage (ou « Suivant ▶ ») : la tête de lecture s'y place.
2. Avancez image par image avec →.
3. Pour voir l'image réelle en pleine résolution, choisissez « Image exacte : rendu final
   flouté ». C'est exactement ce que produira l'export pour cette image. « Source nette »
   montre l'original, utile pour chercher une plaque que le logiciel aurait manquée.
4. Cochez la case du passage quand il est vérifié.

L'onglet **« Zones »** liste les zones présentes sur l'image affichée. Cliquez une zone dans
la liste ou directement dans l'image pour la sélectionner.

| Vous voulez | Faites |
|---|---|
| Ajouter un flou là où rien n'a été détecté | « + Zone manuelle », tracez un rectangle. Il dure 2 s ; placez la tête où il doit commencer puis « Début = cette image », idem pour la fin |
| Faire suivre un objet à une zone manuelle | avancez dans la vidéo, « Déplacer ici… », retracez le rectangle : la zone glisse progressivement d'une position à l'autre |
| Retirer un faux flou (reflet, panneau) | sélectionnez la zone, « Ne pas flouter ». « Rétablir le flou » annule |
| Faire durer un flou plus longtemps | sélectionnez la zone, boutons + de « Avant » ou « Après » |
| Supprimer une zone manuelle | « Supprimer » |

### Compteur

1. Placez-vous sur une image où le compteur est bien visible.
2. Onglet « Zones », bouton « Compteur… », tracez un rectangle autour du compteur (ou seulement
   de l'affichage de la vitesse).
3. Choisissez :
   - **Suivre le compteur** si le cadrage bouge (cas d'une vidéo recadrée depuis le 360). Le
     compteur est suivi image par image, vers l'avant et l'arrière.
   - **Zone fixe** si la caméra est fixée à la moto et que le compteur ne bouge pas dans l'image.
4. Si le suivi décroche, le passage apparaît dans « À vérifier » en rose. Placez-vous au début
   du passage fautif et recommencez l'étape 2 : le suivi repart de là.

### Réglages

Onglet « Réglages ». La partie « Flou » s'applique au prochain export sans rien réanalyser.
La partie « Détection » refait toute l'analyse.

| Réglage | Effet |
|---|---|
| Marge autour des zones | 0,30 = la zone dépasse de 30 % de chaque côté. Augmentez si un bord de plaque dépasse |
| Prolongation avant / après | durée de flou ajoutée avant l'apparition et après la disparition d'un objet |
| Type de flou | mosaïque lissée, ou aplat de couleur (le plus sûr) |
| Forme | ellipse pour les visages, rectangle arrondi pour les plaques par défaut |

### Export

« Exporter… » propose le nom du fichier, la qualité (80 par défaut), le codec (celui de la
source par défaut) et l'encodeur. Cochez « Vidéo de contrôle » pour obtenir une vidéo où les
cadres et les scores sont dessinés à la place du flou : pratique pour repérer ce qui a été
manqué.

## 5. Si l'analyse est trop lente

Dans l'ordre, du moins risqué au plus risqué (onglet « Réglages », partie « Détection ») :

1. Modèles plus légers : `yolo11n-face` et `yolo-v9-t-640`.
2. Tuiles plus grandes : par exemple 1920 pour une vidéo 4K (moins de zones à analyser par
   image, mais les objets lointains sont moins bien vus).
3. « Détecter une image sur 2 » : raisonnable à 60 images par seconde.

« Annuler » pendant une analyse conserve ce qui est fait ; « Analyser » reprend ensuite.

## 6. En ligne de commande

Tout ce que fait l'interface existe aussi en commandes, pour traiter une vidéo sans y toucher :

```sh
uv run moto-blur probe samples/a.mp4                          # caractéristiques de la vidéo
uv run moto-blur run samples/a.mp4 -o samples/a_floute.mp4    # analyse + rendu
uv run moto-blur run samples/a.mp4 -o samples/a_debug.mp4 --debug
uv run moto-blur run samples/a.mp4 -o samples/out.mp4 --trim-start 12.5 --trim-end 8
uv run moto-blur report samples/a.motoblur.json               # passages à vérifier
uv run moto-blur dash samples/a.motoblur.json --at 0:05 --box 1500,1700,600,300
```

Le détail des options est dans le [README](README.md).

## 7. Problèmes fréquents

| Symptôme | Cause et solution |
|---|---|
| `uv : commande introuvable` | fermez et rouvrez le terminal après l'installation de uv |
| La page ne s'ouvre pas | copiez dans le navigateur l'adresse `http://127.0.0.1:…` affichée dans le terminal |
| La vidéo reste noire dans l'interface | l'aperçu est encore en préparation (barre en haut) ; sinon essayez Chrome ou Edge |
| L'analyse est très lente et le bandeau dit `CPU` | l'accélération n'a pas été utilisée ; envoyez-moi la sortie de `uv run moto-blur info` et les messages du terminal |
| « un traitement est déjà en cours » | attendez la fin, ou cliquez « Annuler » |
| L'export refuse : « passages non analysés » | cliquez « Analyser » |
| Les couleurs de l'aperçu sont ternes (vidéo HDR) | seul l'aperçu est concerné ; l'export garde les couleurs de la source |
| Trop de faux flous | montez « Score minimal pour suivre un objet » à 0,30 (au prix d'un risque de fuite plus élevé) |

Fichiers créés à côté de votre vidéo : `nom.motoblur.json` (le projet), `nom.motoblur.proxies/`
(aperçus, supprimables), `nom.motoblur.report.txt` (rapport, en ligne de commande). Les modèles
sont dans `~/.moto-blur/models`.

Pour mettre à jour le logiciel : `git pull` puis `uv sync` dans le dossier `video-editor`.

## 8. L'application de bureau

La même interface existe en application autonome (fenêtre propre, sans terminal ni navigateur).
Les installeurs ne sont pas encore publiés. Pour les fabriquer :

- **Sur GitHub** : onglet « Actions », flux « Installeurs », « Run workflow ». Il produit un
  `.dmg` pour Mac et un installeur `.exe` pour Windows, téléchargeables en bas de la page du
  flux. Ce flux n'a encore jamais tourné : il peut demander une correction au premier essai.
- **Sur votre machine** (il faut Node et Rust) :

  ```sh
  uv run --with pyinstaller pyinstaller desktop/engine.spec --distpath desktop/engine-dist --workpath desktop/engine-build -y
  cd desktop && npm install && npx tauri build
  ```

L'application n'est pas signée (la signature demande un compte développeur Apple payant et un
certificat Windows).

- **macOS** : au premier lancement, le système refuse de l'ouvrir. Ouvrez Réglages Système,
  « Confidentialité et sécurité », descendez jusqu'au message concernant moto-blur et cliquez
  « Ouvrir quand même ». Si le message dit que l'application est « endommagée », tapez dans le
  Terminal : `xattr -cr /Applications/moto-blur.app`, puis relancez-la.
- **Windows** : SmartScreen affiche un avertissement ; cliquez « Informations complémentaires »,
  puis « Exécuter quand même ».

## 9. Ce qui m'aiderait après vos essais

Sur chacune des deux machines, avec un extrait court :

1. La sortie de `uv run moto-blur info` et de `uv run moto-blur probe samples/votre-video.mp4`.
2. Le bandeau affiché à la fin de l'analyse (vitesse et accélération utilisée).
3. La ligne « Encodeur vidéo » affichée dans le terminal pendant l'export.
4. Ce que le logiciel a manqué : plaques ou visages lisibles sans cadre dans la vidéo de
   contrôle, et dans quelles conditions (distance, nuit, pluie, vitesse).
5. Le comportement du suivi du compteur : tient-il sur toute la séquence ?
