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
   vidéo (dans l'application installée, vous pouvez aussi la faire glisser sur la fenêtre). Le
   logiciel prépare un aperçu (une seule fois par vidéo). Votre vidéo d'origine n'est jamais
   modifiée. Si vous avez déjà travaillé sur cette vidéo, vous reprenez où vous en étiez ; vos
   projets récents sont listés sous le bouton. « Libérer de la place » supprime les fichiers
   d'aperçu des anciens projets (ils se refabriquent si vous les rouvrez).

   Si vous avez enregistré vos **réglages habituels** (voir la fin), l'assistant propose de les
   reprendre pour une nouvelle vidéo : il cherche alors lui-même vos objets (compteur…) et les
   suit. Vérifiez quand même qu'il a trouvé le bon objet.
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
4. **Comment est votre vidéo ?** « Normal » convient presque toujours. « Conditions
   difficiles » (nuit, pluie, contre-jour, plaques lointaines) cherche plus finement : analyse
   nettement plus longue, davantage de flous en trop. « Rapide » divise environ par deux la
   durée, mais peut manquer un visage ou une plaque furtifs.

   **Analyse.** « Combien de temps ça va prendre ? » mesure la vitesse sur quelques images et
   annonce la durée. Cliquez « Lancer l'analyse ». Le logiciel cherche les visages et les plaques
   image par image. La durée dépend de l'ordinateur (de quelques minutes à plus d'une heure pour
   une longue vidéo) ; le temps restant s'affiche après quelques secondes. Au tout premier
   lancement, les modèles de détection sont téléchargés (environ 65 Mo). Vous pouvez laisser
   l'ordinateur travailler ; à la fin, l'assistant passe seul à la suite.
5. **Vérification.** Il n'y a rien à valider : **tout ce que le logiciel a trouvé est flouté**.
   Il reste à regarder la vidéo une fois, seul moyen de repérer ce que le logiciel n'a jamais
   vu. Cliquez « Regarder la vidéo ». Elle est lue deux fois plus vite (« Vitesse : Normale /
   2× » sous la vidéo ; « 5 s » revient cinq secondes en arrière ; la barre d'espace met en
   pause). La règle du jeu tient en deux lignes :
   - **Un flou en trop ?** Cliquez dessus sur la vidéo : il disparaît, la lecture continue. Un
     bandeau « Flou retiré » propose « Annuler » pendant quelques secondes et, si le logiciel
     se trompe plusieurs fois au même endroit, « Retirer aussi les N flous au même endroit » et
     « Ne plus jamais flouter cet endroit » (attention : un visage ou une plaque qui s'y
     tiendrait ne serait plus flouté non plus).
   - **Quelque chose reste lisible ?** Appuyez sur « Il manque un flou ici » (ou la touche S).
     La lecture continue : vous réglerez chaque endroit signalé juste après.

   Si vous vous arrêtez en route (« Retour »), l'assistant propose ensuite « Reprendre à … ».
   « Passer cette étape » va directement à l'enregistrement, qui vous rappellera que la vidéo
   n'a pas été regardée.

   Après la relecture, chaque **endroit signalé** tourne en boucle : « Entourer ce qui manque »,
   puis entourez sur la vidéo ce qui doit être caché ; le flou **suit** ensuite ce que vous avez
   entouré, jusqu'au prochain changement de plan. « C'est réglé » passe au suivant. Sous la
   vidéo, une petite barre et les boutons « Image précédente / suivante » (touches ← →)
   permettent de se placer sur la bonne image ; la molette zoome.

   S'il y en a, viennent enfin les **alertes** : les rares endroits où quelque chose peut rester
   visible, par exemple un objet suivi que le logiciel a perdu. La question est « Tout ce qui
   doit être flouté l'est-il ? » :
   - « Oui, c'est bon » : on passe à la suite.
   - « Montrer « compteur » ici » : entourez l'objet ; il est suivi de nouveau sur ce plan.
   - « Il manque un flou » : entourez ce qui doit être caché ; le flou le suit.
   - « Un flou est en trop » : cliquez-le sur la vidéo ; il est retiré.
   - « Plus tard » : l'enregistrement vous le rappellera.

   Sur le premier écran de l'étape, « Retirer des flous en trop d'un coup » ouvre une planche
   facultative : **les flous qui se voient le plus** (gros ou longs), en vignettes. Touchez ceux
   qui sont en trop (un reflet, une affiche), puis « Retirer les N flous choisis ». « Voir tous
   les flous » montre aussi les petits.
6. **Enregistrer la vidéo.** Un résumé rappelle la durée et ce qui est flouté. S'il reste un
   endroit signalé ou une alerte, le logiciel le dit et demande de cocher « Je crée la vidéo
   quand même » ; si la vidéo n'a pas été regardée en entier, il le rappelle sans bloquer.
   Choisissez le format : « Comme l'original » (par défaut), « YouTube », « Réseaux
   sociaux » ou « Fichier léger ». « Essayer d'abord sur 10 secondes » crée un court extrait pour
   contrôler le résultat réel. Cliquez « Créer la vidéo floutée ». À la fin, « Ouvrir le
   dossier » montre le fichier, enregistré à côté de votre vidéo d'origine.

   Sur le dernier écran, « Enregistrer ces réglages pour mes prochaines vidéos » mémorise ce qu'il
   faut flouter, le type de vidéo, vos réglages de flou et l'aspect de vos objets.

« Annuler la dernière action », en haut de l'écran, défait la dernière modification (flou
retiré, flou ajouté, coupe…).

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
5. **Vérifier.** Cliquez « Commencer la relecture ». Tout ce que le logiciel a trouvé est déjà
   flouté ; vous regardez la vidéo une fois, à 2× : un clic sur un flou en trop le retire,
   la touche S signale ce qui reste lisible (détails plus bas, partie « Vérification »).
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
| Changer la vitesse de l'aperçu | menu « 1× » à côté de « Lecture » : de 0,25× à 2× (sans effet sur l'export) | |
| Annuler / rétablir la dernière modification | flèches en haut à droite | Ctrl+Z / Ctrl+Y (Cmd+Z / Maj+Cmd+Z sur Mac) |
| Enregistrer l'image affichée en pleine résolution | appareil photo dans la barre « Lecture » (image floutée ; nette si « Qualité » est sur « image exacte, sans flou ») | |
| Voir le flou / voir les contours des flous | bouton « Voir les contours » / « Voir le flou » | V |
| Zoomer sur la vidéo (petite plaque lointaine) | molette sur la vidéo (ou pincement sur le pavé tactile) ; glisser pour se déplacer ; « Image entière » pour revenir | + et −, 0 pour revenir |
| Voir tous les raccourcis | bouton « ? » en haut | ? |
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
| Changer l'ordre | faites glisser le passage par sa poignée, à gauche de son nom ; ou cliquez-le, puis « Déplacer à gauche » ou « Déplacer à droite » |
| Couper le son sur un passage | tête de lecture au début, « Couper le son : début » (ou M) ; tête à la fin, « Couper le son : fin » |
| Retirer un passage muet | onglet « Flous », en bas, « Retirer » |

Chaque passage montre quelques images du clip et, dessous, le niveau du son.

Chaque modification est enregistrée aussitôt dans le fichier projet ; Ctrl+Z l'annule. Si vous rallongez un
passage après une analyse, le guide propose de nouveau « Analyser » : seule la partie ajoutée
sera analysée.

Si vous assemblez des clips de résolution ou de fréquence d'images différentes, un bandeau vous
demande comment les assembler : sur le format du premier clip, ou sur la plus grande résolution
et la plus haute fréquence. Rien n'est choisi à votre place ; le choix se change ensuite dans
l'onglet « Réglages ». Les clips qui ne sont pas au format retenu sont mis à l'échelle (avec
des bandes noires si le rapport d'image diffère) et leurs images sont dupliquées ou sautées.

### Vérification

**Il n'y a plus rien à valider.** Tout ce que le logiciel a détecté est flouté, sans question :
un flou douteux qui reste en place ne fait courir aucun risque, il est au pire inutile. L'onglet
**« Vérification »** (étape 3) propose, par ordre d'importance :

| Entrée | À quoi elle sert |
|---|---|
| **Relecture** (bouton orange) | La seule étape qui protège vraiment : elle seule montre ce que le logiciel n'a jamais détecté |
| Signalements à traiter | Les endroits que vous avez signalés pendant la relecture |
| Alertes | Les endroits où quelque chose peut rester visible (un objet suivi perdu, par exemple). Rares |
| Flous gênants | Facultatif : retirer d'un coup les flous inutiles qui se voient |
| Non floutés à regarder | Facultatif : ce que le logiciel a vu mais n'a pas flouté, faute de certitude |

Tout est enregistré dans le fichier projet : vous pouvez fermer le logiciel et reprendre plus
tard.

#### Relecture

1. Cliquez « Commencer la relecture » (« Reprendre à … » si vous l'aviez interrompue). Tout le
   montage est lu, flou affiché, à 2× (la vitesse se règle).
2. **Un flou en trop ? Cliquez dessus** : il disparaît et la lecture continue. Un bandeau propose
   alors « Annuler », « Retirer aussi les N flous au même endroit » (la même fausse détection
   qui revient) et « Ne plus jamais flouter cet endroit » (voir plus bas).
3. **Quelque chose reste lisible ? Touche S** (ou « Signaler ici »). La lecture ne s'arrête pas ;
   un repère apparaît sur la timeline.
4. À la fin, traitez les signalements : chacun tourne en boucle, de 1,5 s avant à 1,5 s après.
   « Il manque un flou » : tracez un rectangle, puis « Suivre ce que j'ai entouré » (le flou suit
   l'objet jusqu'au changement de plan) ou « Flou fixe ». « C'est réglé » retire le signalement.

Pendant la relecture, le montage est verrouillé (« Couper ici », « Supprimer le passage »… et
leurs touches), pour éviter une coupe par erreur. « Quitter » le rend.

#### Alertes

Une alerte signale un endroit où quelque chose peut rester visible : l'objet que vous faites
suivre (compteur, téléphone…) a été perdu, ou retrouvé automatiquement après un changement de
plan. Le passage tourne en boucle et vous répondez à « Tout ce qui doit être flouté l'est-il ? » :
« Oui, c'est bon », « Il manque un flou », « Un flou est en trop », « Montrer « compteur » ici »
ou « Plus tard ».

#### Flous gênants et non floutés

« Flous gênants » ouvre une planche de vignettes limitée aux flous qui se voient (gros ou
longs), les plus gênants d'abord. Cochez ceux qui n'ont pas lieu d'être (Maj + clic pour une
série), puis « Retirer les N flous cochés » ; Ctrl+Z remet tout. « Tous les flous » montre aussi
les petits.

« Non floutés à regarder » montre ce que le logiciel a vu sans le flouter : objets trop peu
sûrs, ou que la contre-vérification n'a pas retrouvés (voir « Réglages »). Si un vrai visage
s'y trouve, cochez-le puis « Flouter quand même ».

#### Ne jamais flouter un endroit

Si le logiciel prend toujours le même morceau de votre moto pour un visage ou une plaque :
onglet « Flous », « Ne jamais flouter ici… », tracez un rectangle. Les flous automatiques qui
s'y tiennent sont retirés, maintenant et après une nouvelle analyse. Un objet qui ne fait que
traverser la zone reste flouté. **Attention** : un vrai visage ou une vraie plaque qui se
tiendrait dans cette zone ne serait pas flouté. La liste « Endroits jamais floutés » permet de
retirer une zone.

#### Voir une image de près, exporter

Pour voir une image réelle en pleine résolution, mettez en pause et choisissez « Qualité :
image exacte, floutée ». C'est exactement ce que produira l'export pour cette image. « Image
exacte, sans flou » montre l'original, utile pour chercher une plaque que le logiciel aurait
manquée. Zoomez à la molette pour regarder de près.

L'étape « 4 Export » dit ce qui reste : relecture non faite, signalements à traiter, alertes à
regarder. Dans ce cas seulement, le logiciel vous demande de cocher « J'exporte quand même »
avant d'exporter.

#### Travail libre sur les flous

L'onglet **« Flous »** liste les flous présents sur l'image affichée, avec une vignette et un
niveau de confiance (sûr, à vérifier, douteux), du plus douteux au plus sûr ; les boutons
« Visages », « Plaques », « Objets » filtrent la liste. Cliquez un flou dans la liste ou
directement dans l'image pour le sélectionner. La légende sous la vidéo explique les couleurs.

| Vous voulez | Faites |
|---|---|
| Ajouter un flou qui suit un objet | « Ajouter un flou », tracez un rectangle, « Suivre ce que j'ai entouré » |
| Ajouter un flou fixe là où rien n'a été détecté | « Ajouter un flou », tracez un rectangle, « Flou fixe ». Il dure 2 s ; placez la tête où il doit commencer puis « Début = cette image », idem pour la fin |
| Déplacer un flou fixe ajouté | avancez dans la vidéo, « Déplacer ici… », retracez le rectangle : le flou glisse progressivement d'une position à l'autre |
| Retirer un faux flou (reflet, panneau) | sélectionnez le flou, « Ne pas flouter ». « Rétablir le flou » annule |
| Faire durer un flou plus longtemps | sélectionnez le flou, boutons + de « Avant » ou « Après » |
| Supprimer un flou ajouté | « Supprimer » |

### Compteur, téléphone et autres objets

Le logiciel ne détecte seul que les visages et les plaques. Tout autre objet à cacher (compteur,
téléphone, GPS…) se désigne à la main, une fois par clip ; chaque objet porte un nom.

1. Placez-vous sur une image où l'objet est bien visible.
2. Onglet « Flous », bouton « Suivre un objet… », tracez un rectangle autour de l'objet (pour le
   compteur, l'affichage de la vitesse peut suffire).
3. Donnez son nom : « Compteur », « Téléphone », « GPS », ou un autre nom tapé dans le champ.
4. Choisissez :
   - **Suivre l'objet** s'il bouge dans l'image (cas d'une vidéo recadrée depuis le 360). Il est
     suivi image par image, vers l'avant et l'arrière.
   - **Flou fixe** si la caméra est fixée à la moto et que l'objet ne bouge pas dans l'image.
5. À chaque changement de plan, le suivi s'arrête : il ne glisse plus sur un autre objet (un
   rétroviseur, par exemple). Le logiciel cherche ensuite l'objet dans le nouveau plan et, s'il le
   retrouve avec une forte ressemblance, reprend le suivi. Deux cas sont signalés à la
   vérification :
   - « retrouvé automatiquement » : vérifiez que le flou est bien sur l'objet ;
   - « n'est plus suivi ici » : l'objet n'a pas été retrouvé, rien n'y est flouté.
6. Pour corriger un plan, cliquez « Montrer « compteur » ici » sur la carte de vérification (ou
   recommencez l'étape 2 avec le même nom) et entourez l'objet : son suivi est refait sur ce plan
   seulement, jusqu'au changement de plan suivant ; le reste est conservé. Pour tout
   recommencer, supprimez d'abord l'objet dans « Objets suivis ».

**Contour précis.** Sur un objet suivi, « Contour précis » calcule la silhouette exacte de
l'objet : le flou l'épouse au lieu d'un rectangle. Au premier usage, un modèle de 155 Mo est
téléchargé ; le calcul prend du temps (de l'ordre d'une seconde par quart de seconde de vidéo
suivie sur un processeur ; vitesse sur votre carte graphique non mesurée). Là où le contour
n'est pas fiable, la forme habituelle est gardée. Un contour serré protège moins qu'un rectangle
large : vérifiez le résultat. « Revenir à la forme simple » l'annule.

Reprendre un nom déjà utilisé dans le clip remplace les zones de cet objet (le dialogue le dit) ;
un autre nom ajoute un objet. Le bloc « Objets suivis », en bas de l'onglet « Flous », liste les
objets du clip affiché ; « Supprimer » retire toutes les zones d'un objet, après confirmation.

### Réglages

Onglet « Réglages ». La partie « Flou » s'applique au prochain export sans rien réanalyser.
La partie « Détection » refait toute l'analyse ; le plus simple est d'y choisir un préréglage
(« Normal », « Conditions difficiles », « Rapide », décrits dans le mode simple plus haut).
« Personnalisé » s'affiche si vous avez modifié un réglage de détection à la main. Avant de
lancer l'analyse, « Estimer la durée » annonce le temps qu'elle prendra.

**Profil « ma moto ».** « Enregistrer ce projet comme profil » mémorise vos réglages et l'aspect
de vos objets suivis ; « Appliquer mon profil à ce projet » les reprend sur une nouvelle vidéo,
en cherchant vos objets automatiquement.

| Réglage | Effet |
|---|---|
| Marge autour des flous | en pourcentage : le flou dépasse de l'objet de chaque côté. Augmentez si un bord de plaque dépasse |
| Garder le flou avant et après | en secondes : durée de flou ajoutée avant l'apparition et après la disparition d'un objet |
| Type de flou | mosaïque lissée, ou aplat de couleur (le plus sûr) |
| Forme | ellipse pour les visages, rectangle arrondi pour les plaques par défaut |
| Contre-vérifier les flous douteux | coché par défaut : un objet douteux est remontré au détecteur en plus grand ; s'il n'y est pas retrouvé, il n'est pas flouté (arbre, reflet, sac pris pour un visage…). Il reste dans « Non floutés à regarder ». Décochez si un vrai visage ou une vraie plaque manque |
| Stabiliser les flous | coché par défaut : le flou ne tremble plus et ne change plus de taille à chaque image, sans jamais découvrir l'objet |
| Moins de faux flous ↔ Moins de risques | un seul curseur à cinq crans, qui règle le score minimal des visages et des plaques. Vers la droite, le logiciel floute davantage dans le doute. Le changement s'applique tout de suite, sans réanalyse |
| Réglages experts (repliés) | les curseurs détaillés ci-dessous, et toute la partie « Détection » (modèles, tuiles, seuils) |
| Score minimal des visages, des plaques | un objet n'est flouté que si elle atteint ce minimum (0,40 pour les visages, 0,25 pour les plaques au départ). Plus haut : moins de faux flous, mais un vrai visage peu net peut être manqué. Le changement s'applique tout de suite, sans réanalyse ; le nombre de détections écartées s'affiche sous le curseur |
| Images sûres exigées | le score minimal doit être atteint sur ce nombre d'images (3 au départ), ou une seule fois avec un score très net. Les fausses détections, qui clignotent sur une ou deux images, sont ainsi écartées |

Les objets trop peu sûrs ne sont pas floutés. Pour les voir, ouvrez « Non floutés (trop peu
sûrs) » dans l'onglet « Flous » : ils sont entourés en gris sur l'image, et « Flouter quand
même » en rétablit un (« Ne plus flouter quand même » annule).

### Export

À l'étape « 4 Export », « Exporter… » propose le nom du fichier et le format : « Comme
l'original » (par défaut), « YouTube », « Réseaux sociaux » (1920 pixels au plus) ou « Fichier
léger » (1280 pixels). « Réglages détaillés » donne la qualité, le codec et l'encodeur.
« Essayer sur un extrait de 10 s » exporte seulement les 10 secondes autour de la tête de
lecture (fichier `…_extrait.mp4`), pour contrôler le résultat réel avant le rendu complet. Si
vous n'avez pas fait la relecture complète, le dialogue le rappelle. Cochez « Vidéo de contrôle » pour obtenir une vidéo où les
cadres et les scores sont dessinés à la place du flou : pratique pour repérer ce qui a été
manqué.

### Images d'entraînement (plaques)

Réglages › Détection › « Exporter des images d'entraînement (plaques) » écrit, à côté du projet,
des images tirées des plaques que vous avez vérifiées. Elles servent à entraîner un modèle de
plaques sur vos propres vidéos : marche à suivre dans `docs/ENTRAINEMENT.md`. Ces images
montrent des plaques non floutées : ne les publiez pas.

## 6. Si l'analyse est trop lente

Le plus simple : le préréglage « Rapide ». Sinon, dans l'ordre, du moins risqué au plus risqué
(onglet « Réglages », partie « Détection ») :

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
| Trop de faux flous | dans « Réglages », montez « Score minimal des visages » ou « Images sûres exigées » ; l'effet est immédiat, sans réanalyse (au prix d'un risque de fuite plus élevé) |

Fichiers créés à côté de votre vidéo : `nom.motoblur.json` (le projet), `nom.motoblur.proxies/`
(aperçus, supprimables), `nom.motoblur.report.txt` (rapport, en ligne de commande). Les modèles
sont dans `~/.moto-blur/models`.

Pour mettre à jour le logiciel : `git pull` puis `uv sync` dans le dossier `video-editor`.

## 9. L'application de bureau

La même interface existe en application autonome : une fenêtre propre, sans terminal ni
navigateur, et sans rien installer d'autre (ni uv, ni Python).

### Installer

1. Ouvrez la page des versions : <https://github.com/ALapaque/video-editor/releases/latest>
   (pas besoin de compte GitHub).
2. Téléchargez :
   - **Windows** : le fichier qui finit par `_x64-setup.exe`, puis lancez-le ;
   - **Mac (puce Apple)** : le fichier `.dmg`, ouvrez-le et glissez moto-blur dans Applications.
3. L'application n'est pas signée (la signature est payante), d'où un avertissement la
   première fois seulement :
   - **Windows** : « Windows a protégé votre ordinateur » : cliquez « Informations
     complémentaires », puis « Exécuter quand même » ;
   - **Mac** : au premier lancement, le système refuse de l'ouvrir. Ouvrez Réglages Système,
     « Confidentialité et sécurité », descendez jusqu'au message concernant moto-blur et
     cliquez « Ouvrir quand même ». Si le message dit que l'application est « endommagée »,
     tapez dans le Terminal : `xattr -cr /Applications/moto-blur.app`, puis relancez-la.

### Mises à jour

Au démarrage, moto-blur regarde s'il existe une version plus récente sur la page des versions.
Si oui, il propose « Mettre à jour » ou « Plus tard » ; rien n'est téléchargé sans votre
accord. Le paquet est vérifié par sa signature avant d'être installé, puis l'application
redémarre ; vos projets sont conservés. C'est le seul appel réseau en dehors du premier
téléchargement des modèles : le traitement des vidéos reste entièrement local. Pour
désactiver cette vérification, lancez l'application avec la variable `MOTO_BLUR_NO_UPDATE=1`.

### Si l'application ne démarre pas

L'application installée n'a été essayée que sous Linux, jamais sur un vrai Mac ni un vrai
PC. Si elle ne démarre pas, utilisez `uv run moto-blur ui` (section 2), qui offre exactement
la même interface, et dites-moi ce qui s'affiche.

### Publier une nouvelle version (pour le développeur)

Une seule fois : dans GitHub, **Settings > Secrets and variables > Actions > New repository
secret**, créez le secret `TAURI_SIGNING_PRIVATE_KEY` avec le contenu de la clé privée de
signature des mises à jour. **Gardez une copie de cette clé** : sans elle, les applications
déjà installées ne pourront plus se mettre à jour (il faudrait les réinstaller à la main).

Ensuite, **rien à faire** : chaque fusion dans `main` qui modifie l'application, le moteur ou
l'interface publie automatiquement une nouvelle version (v0.2.0, puis v0.2.1, v0.2.2…).
GitHub fabrique l'installeur Windows et l'image Mac (une quinzaine de minutes) ; la version
n'apparaît sur la page des versions, et n'est proposée aux applications installées, que si
les deux ont réussi. Une fusion qui ne touche que la documentation ne publie rien.

Pour passer à un autre numéro (v0.3.0, v1.0.0…), poussez vous-même l'étiquette voulue depuis
`main` ; les versions automatiques suivantes repartiront de là :

```sh
git tag v0.3.0
git push origin v0.3.0
```

Suivi des fabrications : <https://github.com/ALapaque/video-editor/actions/workflows/installeurs.yml>.

Pour seulement fabriquer les installeurs sans rien publier : onglet **Actions**, « Installeurs »,
« Run workflow » ; ils sont téléchargeables en bas de la page de l'exécution (« Artifacts »).

Pour les fabriquer sur sa propre machine (il faut Node et Rust) :

```sh
uv run --with pyinstaller pyinstaller desktop/engine.spec --distpath desktop/engine-dist --workpath desktop/engine-build -y
cd desktop && npm install && TAURI_SIGNING_PRIVATE_KEY="$(cat chemin/vers/la/cle)" npx tauri build
```

## 10. Ce qui m'aiderait après vos essais

Sur chacune des deux machines, avec un extrait court :

1. La sortie de `uv run moto-blur info` et de `uv run moto-blur probe samples/votre-video.mp4`.
2. Le bandeau affiché à la fin de l'analyse (vitesse et accélération utilisée).
3. La ligne « Encodeur vidéo » affichée dans le terminal pendant l'export.
4. Ce que le logiciel a manqué : plaques ou visages lisibles sans cadre dans la vidéo de
   contrôle, et dans quelles conditions (distance, nuit, pluie, vitesse).
5. Le comportement du suivi du compteur : tient-il sur toute la séquence ?
