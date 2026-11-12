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
git clone -b main https://github.com/ALapaque/video-editor.git
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
git clone -b main https://github.com/ALapaque/video-editor.git
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

Au premier lancement, le logiciel demande comment vous voulez travailler :

- **Mode simple** (recommandé pour commencer) : un assistant vous guide pas à pas, une question
  par écran, un seul gros bouton à la fois. Pas de timeline, pas de réglages.
- **Mode avancé** : tous les outils (timeline, coupes au milieu, plusieurs vidéos, réglages).

Le choix est mémorisé. Vous pouvez changer à tout moment avec « Passer en mode avancé » ou
« Passer en mode simple », en haut à droite : le projet ouvert reste ouvert et rien n'est perdu.

## 3. Le mode simple, pas à pas (recommandé)

Six étapes s'affichent en haut de la fenêtre. Sur chaque écran : une question, un bouton orange
(la touche Entrée fait la même chose) et « Retour » en haut à gauche (ou la touche Échap).

**Pour un premier essai, prenez une vidéo courte**, ou ne gardez qu'une minute à l'étape 3 :
l'analyse ne porte que sur la partie gardée.

1. **Quelle vidéo voulez-vous flouter ?** Cliquez « Choisir une vidéo… » et cliquez votre
   vidéo. Le logiciel prépare un aperçu (une seule fois par vidéo). Votre vidéo d'origine n'est
   jamais modifiée. Si vous avez déjà travaillé sur cette vidéo, vous reprenez où vous en étiez.
2. **Que faut-il flouter ?** Cochez « Les visages », « Les plaques d'immatriculation » et/ou
   « Un objet précis » (compteur, téléphone, GPS…), puis « Continuer ».
   Si vous avez coché « Un objet précis » : avec le curseur sous la vidéo, choisissez un moment
   où l'objet se voit bien, puis **entourez-le avec la souris** (cliquez, glissez, relâchez).
   Donnez-lui un nom et répondez à « L'objet bouge-t-il dans l'image ? » : « Oui » (le logiciel
   le suit dans toute la vidéo) ou « Non, il reste au même endroit » (caméra fixée à la moto).
   Le résultat est montré quelques secondes : « C'est bon », « Recommencer » ou « Ajouter un
   autre objet ».
3. **Voulez-vous couper le début ou la fin ?** Faites glisser les deux poignées orange sous la
   vidéo ; ce qui est grisé sera retiré. « Lire l'extrait » montre ce qui reste. Puis « Garder
   cette partie », ou « Ne rien couper ».
4. **Analyse.** Cliquez « Lancer l'analyse ». Le logiciel cherche les visages et les plaques
   image par image. La durée dépend de l'ordinateur (de quelques minutes à plus d'une heure pour
   une longue vidéo) ; le temps restant s'affiche après quelques secondes. Au tout premier
   lancement, les modèles de détection sont téléchargés (environ 65 Mo). Vous pouvez laisser
   l'ordinateur travailler ; à la fin, l'assistant passe seul à la suite.
5. **Vérification.** Le logiciel vous montre un par un les endroits où il a hésité, en boucle,
   flou affiché, avec une seule question : « Tout ce qui doit être flouté l'est-il ? ».
   - « Oui, c'est bon » : on passe au suivant.
   - « Il manque un flou » : entourez sur la vidéo ce qui doit être caché.
   - « Un flou est en trop » : cliquez-le sur la vidéo ; il est retiré.
   - « Plus tard » : on y reviendra.

   Ensuite vient la question **« Voulez-vous tout regarder une dernière fois ? »**. Répondez oui
   si la vidéo doit être publiée : c'est le seul moyen de repérer ce que le logiciel n'a jamais
   vu. Pendant la lecture, appuyez sur « Signaler un problème ici » (ou la touche S) dès que
   quelque chose reste lisible ; vous corrigerez chaque endroit signalé juste après.
6. **Enregistrer la vidéo.** Un résumé rappelle la durée, ce qui est flouté et ce qui reste à
   revoir. Cliquez « Créer la vidéo floutée ». À la fin, « Ouvrir le dossier » montre le fichier,
   enregistré à côté de votre vidéo d'origine.

Le mode simple ne coupe que le début et la fin d'une seule vidéo. Pour couper au milieu,
assembler plusieurs vidéos, couper le son d'un passage ou changer les réglages, passez en mode
avancé : la suite de ce guide le décrit.

## 4. Le mode avancé : premier essai, pas à pas

En haut de la fenêtre, quatre étapes : **1 Montage, 2 Analyse, 3 Vérification, 4 Export**. Juste
au-dessus de la vidéo, une ligne dit quoi faire maintenant et propose le bouton de l'action
suivante. Ce guide ne bloque rien : vous pouvez cliquer n'importe quelle étape à tout moment.

**Commencez par un extrait de 30 secondes à 1 minute**, pas par une vidéo entière. L'analyse
ne porte que sur les passages gardés : il suffit donc de ne garder qu'un court passage pour
tester vite.

1. **Ouvrir.** Cliquez « Ouvrir une vidéo ou un projet… », naviguez jusqu'à `samples`, cliquez
   la vidéo. Un fichier projet `ma-video.motoblur.json` est créé à côté d'elle. Vos vidéos
   d'origine ne sont jamais modifiées.
2. **Attendre l'aperçu.** Le logiciel fabrique une copie légère de la vidéo pour une lecture
   fluide. Une fenêtre de progression s'ouvre : pourcentage, images traitées, vitesse, temps
   restant. « Réduire » (ou Échap) la ferme sans arrêter le traitement ; un clic sur la barre de
   progression au-dessus de la vidéo la rouvre. Pour une vidéo 4K de 10 minutes, comptez quelques
   minutes ; ce n'est fait qu'une fois par vidéo.
3. **Ne garder qu'un extrait.** Placez la tête de lecture (clic dans la timeline) au début du
   passage voulu, cliquez « Couper ici ». Placez-la à la fin, « Couper ici » encore.
   Cliquez les passages à jeter puis « Supprimer le passage ».
4. **Analyser.** Cliquez « Analyser » (bouton orange, au-dessus de la vidéo). Au premier lancement, les modèles de
   détection sont téléchargés (environ 65 Mo). Le logiciel mesure ensuite, pendant quelques
   secondes, l'accélération matérielle contre le processeur et garde le plus rapide. À la fin,
   la fenêtre de progression indique la vitesse en images par seconde, ce qui a servi (`CoreML`, `Dml` ou
   `CPU`) et les deux temps mesurés. **Notez cette ligne**, elle m'intéresse.
5. **Vérifier.** Cliquez « Commencer la vérification ». Le logiciel vous montre un par un les
   passages où il a hésité : chacun tourne en boucle avec le flou affiché, et vous répondez à
   une seule question, « Tout ce qui doit être flouté l'est-il ? ». Faites ensuite la
   « Relecture complète » (détails plus bas, partie « Vérification »).
6. **Exporter.** Cliquez « Passer à l'export » puis « Exporter… », gardez les réglages, validez.
   La vidéo est écrite dans le dossier du projet.

## 5. L'interface en détail

### Lecture et déplacement

| Action | Souris | Clavier |
|---|---|---|
| Lecture / pause | bouton « Lecture » | Espace |
| Image précédente / suivante | `◀|` et `|▶` | ← et → |
| Reculer / avancer d'une seconde | | Maj + ← et Maj + → |
| Aller à un instant | clic ou glisser dans la timeline | Début, Fin |
| Ralentir l'aperçu | menu « 1× » à côté de « Lecture » : 0,5× ou 0,25× (sans effet sur l'export) | |
| Régler le volume de l'aperçu | curseur à côté du haut-parleur (50 % au départ) ; un clic sur le haut-parleur coupe ou rétablit le son | |

Sous la vidéo, vous lisez l'instant dans le montage, le nom du clip, le numéro d'image et
l'instant dans le fichier source. Le volume ne concerne que l'écoute dans l'aperçu : il ne change
ni le projet ni la vidéo exportée (pour couper le son d'un passage à l'export, voir « Muet » plus bas).

### Montage

| Vous voulez | Faites |
|---|---|
| Rogner le début ou la fin | glissez le bord gauche ou droit du passage dans la timeline |
| Supprimer un passage au milieu | « Couper ici » au début et à la fin, cliquez le passage, « Supprimer le passage » (ou touche Suppr) |
| Récupérer ce qui a été coupé | glissez le bord du passage voisin vers l'extérieur |
| Ajouter un autre clip | « Ajouter un clip… » ; il se place à la fin |
| Changer l'ordre | cliquez un passage, puis « ◀ Déplacer » ou « Déplacer ▶ » |
| Couper le son sur un passage | tête de lecture au début, « Muet : début » (ou M) ; tête à la fin, « Muet : fin » |
| Retirer un passage muet | panneau « Zones », en bas, « Retirer » |

Chaque modification est enregistrée aussitôt dans le fichier projet. Si vous rallongez un
passage après une analyse, le guide propose de nouveau « Analyser » : seule la partie ajoutée
sera analysée.

Si vous assemblez des clips de résolution ou de fréquence d'images différentes, un bandeau vous
demande comment les assembler : sur le format du premier clip, ou sur la plus grande résolution
et la plus haute fréquence. Rien n'est choisi à votre place ; le choix se change ensuite dans
l'onglet « Réglages ». Les clips qui ne sont pas au format retenu sont mis à l'échelle (avec
des bandes noires si le rapport d'image diffère) et leurs images sont dupliquées ou sautées.

### Vérification

La vérification se fait dans l'onglet **« Vérification »** (étape 3), en deux temps. Tout ce
que vous validez ou signalez est enregistré dans le fichier projet : vous pouvez fermer le
logiciel et reprendre plus tard.

**Important :** le logiciel ne signale que ce qu'il a détecté. Un visage ou une plaque qu'il
n'a jamais vu n'apparaît dans aucune liste. Pour ne rien laisser passer, faites les deux temps.

#### 1. Passages douteux

Ce sont les endroits où le logiciel a hésité : objet très petit, vu très brièvement, perdu un
instant, coupé par le bord de l'image… Ils sont floutés comme les autres, et marqués en orange
sous la timeline (en rose pour le compteur).

1. Cliquez « Commencer la vérification » (ou « Passages douteux »).
2. Le premier passage tourne en boucle, flou affiché ; ses zones sont entourées. Une phrase
   explique pourquoi le logiciel a hésité. Ralentissez si besoin : « Vitesse » 1×, 0,5× ou 0,25×.
3. Répondez à la question « Tout ce qui doit être flouté l'est-il ? » :

| Réponse | Effet |
|---|---|
| « Oui, c'est bon » (touche Entrée) | le passage est validé, le suivant s'affiche |
| « Il manque un flou » | la lecture s'arrête ; tracez un rectangle sur l'image. Il couvre tout le passage, puis la boucle reprend pour que vous contrôliez. Si le logiciel a écarté des détections à cet endroit (score trop faible), il propose de les afficher : « Flouter quand même » suffit alors |
| « Un flou est en trop » | la lecture s'arrête ; cliquez le flou à retirer dans l'image. « Rétablir » l'annule |
| « Plus tard » | passe au suivant sans valider ; le passage reste « à revoir » |
| « Précédent » | revient au passage d'avant |

4. À la fin, un bilan indique combien de passages sont validés et combien restent à revoir.

Pendant la vérification (passages douteux ou relecture), le montage est verrouillé : « Couper
ici », « Supprimer le passage », « Déplacer », « Muet » et leurs touches (C, Suppr, M) sont
inactifs, pour éviter une coupe par erreur. « Quitter » les rend.

Si vous corrigez les zones d'un passage déjà validé (flou retiré ou rétabli), il vous est
redemandé : c'est voulu.

#### 2. Relecture complète

1. Cliquez « Relecture complète ». Tout le montage est lu, flou affiché (ralentissez avec
   « Vitesse » si la scène est chargée).
2. Dès qu'un visage, une plaque ou le compteur reste lisible, cliquez « Signaler ici » ou
   appuyez sur **S**. La lecture ne s'arrête pas ; un repère rouge apparaît sur la timeline.
3. À la fin (ou après « Arrêter la relecture »), cliquez « Traiter les signalements ». Chaque
   signalement tourne en boucle, de 1,5 s avant à 1,5 s après, avec les mêmes boutons que pour
   les passages douteux. « C'est réglé » retire le signalement.

#### Voir une image de près, exporter

Pour voir une image réelle en pleine résolution, mettez en pause et choisissez « Image exacte :
rendu final flouté ». C'est exactement ce que produira l'export pour cette image. « Source
nette » montre l'original, utile pour chercher une plaque que le logiciel aurait manquée.

L'étape « 4 Export » résume la vérification (par exemple « 12 passages validés, 0 à revoir,
0 signalement »). S'il reste des passages à revoir ou des signalements, le logiciel le dit et
vous demande de cocher « J'exporte quand même, sans avoir tout vérifié » avant d'exporter.

#### Travail libre sur les zones

L'onglet **« Zones »** liste les zones présentes sur l'image affichée. Cliquez une zone dans
la liste ou directement dans l'image pour la sélectionner.

| Vous voulez | Faites |
|---|---|
| Ajouter un flou là où rien n'a été détecté | « + Zone manuelle », tracez un rectangle. Il dure 2 s ; placez la tête où il doit commencer puis « Début = cette image », idem pour la fin |
| Faire suivre un objet à une zone manuelle | avancez dans la vidéo, « Déplacer ici… », retracez le rectangle : la zone glisse progressivement d'une position à l'autre |
| Retirer un faux flou (reflet, panneau) | sélectionnez la zone, « Ne pas flouter ». « Rétablir le flou » annule |
| Faire durer un flou plus longtemps | sélectionnez la zone, boutons + de « Avant » ou « Après » |
| Supprimer une zone manuelle | « Supprimer » |

### Compteur, téléphone et autres objets

Le logiciel ne détecte seul que les visages et les plaques. Tout autre objet à cacher (compteur,
téléphone, GPS…) se désigne à la main, une fois par clip ; chaque objet porte un nom.

1. Placez-vous sur une image où l'objet est bien visible.
2. Onglet « Zones », bouton « Suivre un objet… », tracez un rectangle autour de l'objet (pour le
   compteur, l'affichage de la vitesse peut suffire).
3. Donnez son nom : « Compteur », « Téléphone », « GPS », ou un autre nom tapé dans le champ.
4. Choisissez :
   - **Suivre l'objet** s'il bouge dans l'image (cas d'une vidéo recadrée depuis le 360). Il est
     suivi image par image, vers l'avant et l'arrière.
   - **Zone fixe** si la caméra est fixée à la moto et que l'objet ne bouge pas dans l'image.
5. Si le suivi décroche, le passage apparaît parmi les « Passages douteux », en rose sur la
   timeline. Placez-vous au début du passage fautif et recommencez l'étape 2 avec le même nom :
   le suivi repart de là.

Reprendre un nom déjà utilisé dans le clip remplace les zones de cet objet (le dialogue le dit) ;
un autre nom ajoute un objet. Le bloc « Objets suivis », en bas de l'onglet « Zones », liste les
objets du clip affiché ; « Supprimer » retire toutes les zones d'un objet, après confirmation.

### Réglages

Onglet « Réglages ». La partie « Flou » s'applique au prochain export sans rien réanalyser.
La partie « Détection » refait toute l'analyse.

| Réglage | Effet |
|---|---|
| Marge autour des zones | 0,30 = la zone dépasse de 30 % de chaque côté. Augmentez si un bord de plaque dépasse |
| Prolongation avant / après | durée de flou ajoutée avant l'apparition et après la disparition d'un objet |
| Type de flou | mosaïque lissée, ou aplat de couleur (le plus sûr) |
| Forme | ellipse pour les visages, rectangle arrondi pour les plaques par défaut |
| Score minimal des visages, des plaques | une détection n'est floutée que si son meilleur score atteint ce minimum (0,40 pour les visages, 0,25 pour les plaques au départ). Plus haut : moins de faux flous, mais un vrai visage peu net peut être manqué. Le changement s'applique tout de suite, sans réanalyse ; le nombre de détections écartées s'affiche sous le curseur |

Les détections écartées par le score minimal ne sont pas floutées. Pour les voir, ouvrez
« Détections écartées » dans l'onglet « Zones » : elles sont entourées en gris sur l'image, et
« Flouter quand même » en rétablit une (elle porte alors l'étiquette « forcée » ; « Ne plus
forcer » annule).

### Export

À l'étape « 4 Export », « Exporter… » propose le nom du fichier, la qualité (80 par défaut), le codec (celui de la
source par défaut) et l'encodeur. Cochez « Vidéo de contrôle » pour obtenir une vidéo où les
cadres et les scores sont dessinés à la place du flou : pratique pour repérer ce qui a été
manqué.

## 6. Si l'analyse est trop lente

Dans l'ordre, du moins risqué au plus risqué (onglet « Réglages », partie « Détection ») :

1. Modèles plus légers : `yolo11n-face` et `yolo-v9-t-640`.
2. Tuiles plus grandes : par exemple 1920 pour une vidéo 4K (moins de zones à analyser par
   image, mais les objets lointains sont moins bien vus).
3. « Détecter une image sur 2 » : raisonnable à 60 images par seconde.

« Annuler » pendant une analyse conserve ce qui est fait ; « Analyser » reprend ensuite. Pour une
analyse ou un export, « Annuler » demande une confirmation. À la fin d'un traitement, la fenêtre
de progression affiche le résultat (pour un export : le chemin du fichier écrit).

## 7. En ligne de commande

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

## 8. Problèmes fréquents

| Symptôme | Cause et solution |
|---|---|
| `uv : commande introuvable` | fermez et rouvrez le terminal après l'installation de uv |
| La page ne s'ouvre pas | copiez dans le navigateur l'adresse `http://127.0.0.1:…` affichée dans le terminal |
| La vidéo reste noire dans l'interface | l'aperçu est encore en préparation (barre en haut) ; sinon essayez Chrome ou Edge |
| L'analyse est très lente et le bandeau dit `CPU` | l'accélération n'a pas été utilisée ; envoyez-moi la sortie de `uv run moto-blur info` et les messages du terminal |
| « un traitement est déjà en cours » | attendez la fin, ou cliquez « Annuler » |
| L'export refuse : « passages non analysés » | cliquez l'étape « 2 Analyse » puis « Analyser » |
| Les couleurs de l'aperçu sont ternes (vidéo HDR) | seul l'aperçu est concerné ; l'export garde les couleurs de la source |
| Trop de faux flous | montez « Score minimal pour suivre un objet » à 0,30 (au prix d'un risque de fuite plus élevé) |

Fichiers créés à côté de votre vidéo : `nom.motoblur.json` (le projet), `nom.motoblur.proxies/`
(aperçus, supprimables), `nom.motoblur.report.txt` (rapport, en ligne de commande). Les modèles
sont dans `~/.moto-blur/models`.

Pour mettre à jour le logiciel : `git pull` puis `uv sync` dans le dossier `video-editor`.

## 9. L'application de bureau

La même interface existe en application autonome : une fenêtre propre, sans terminal ni
navigateur, et sans rien installer d'autre (ni uv, ni Python).

Les installeurs sont fabriqués automatiquement par GitHub :

1. Ouvrez <https://github.com/ALapaque/video-editor/actions/workflows/installeurs.yml>
   (il faut être connecté à GitHub).
2. Cliquez la ligne la plus récente marquée d'une coche verte.
3. En bas de la page, section « Artifacts », téléchargez `moto-blur-macos-latest` (contient le
   `.dmg` pour Mac Apple Silicon) ou `moto-blur-windows-latest` (contient l'installeur `.exe`).
4. Décompressez le fichier ZIP téléchargé, puis ouvrez le `.dmg` ou lancez le `.exe`.

Ces installeurs ont été fabriqués avec succès, mais l'application installée n'a jamais été
lancée sur un vrai Mac ni un vrai PC : seule sa version Linux l'a été. Si elle ne démarre pas,
utilisez `uv run moto-blur ui` (section 2), qui offre exactement la même interface, et
dites-moi ce qui s'affiche.

Pour les fabriquer vous-même (il faut Node et Rust) :

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

## 10. Ce qui m'aiderait après vos essais

Sur chacune des deux machines, avec un extrait court :

1. La sortie de `uv run moto-blur info` et de `uv run moto-blur probe samples/votre-video.mp4`.
2. Le bandeau affiché à la fin de l'analyse (vitesse et accélération utilisée).
3. La ligne « Encodeur vidéo » affichée dans le terminal pendant l'export.
4. Ce que le logiciel a manqué : plaques ou visages lisibles sans cadre dans la vidéo de
   contrôle, et dans quelles conditions (distance, nuit, pluie, vitesse).
5. Le comportement du suivi du compteur : tient-il sur toute la séquence ?
