# moto-blur

Floutage automatique des visages et des plaques d'immatriculation dans des vidéos moto
(Insta360 exportée « à plat », ou n'importe quelle action cam), avec un montage simple :
rogner, supprimer des passages, assembler plusieurs clips, couper le son.

Tout le traitement est local : aucune vidéo ne quitte la machine. Le seul appel réseau est le
téléchargement des modèles de détection au premier lancement.

**État : phase 1, prototype en ligne de commande.** Le floutage du compteur (phase 2),
l'interface de montage et de vérification (phase 3) et l'application de bureau (phase 4) ne
sont pas encore faits.

> **À lire avant de publier une vidéo.** Aucun logiciel automatique ne garantit « zéro fuite ».
> moto-blur comble les trous de détection, prolonge les flous et élargit les zones, mais un
> objet que le détecteur n'a **jamais** vu (plaque minuscule, de nuit, sous la pluie) n'est ni
> flouté ni signalé. Regardez la vidéo debug et la vidéo finale avant de publier.

## Installation

Il faut [uv](https://docs.astral.sh/uv/), qui installe la bonne version de Python et les
dépendances.

**macOS** (Terminal) :

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/ALapaque/video-editor.git
cd video-editor
uv sync
```

**Windows 10 ou 11** (PowerShell) :

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/ALapaque/video-editor.git
cd video-editor
uv sync
```

Vérifiez ensuite ce que la machine sait accélérer :

```sh
uv run moto-blur info
```

La ligne « ONNX Runtime » doit contenir `CoreML` sur Mac et `Dml` (DirectML) sur Windows.
Sinon l'analyse tourne sur le processeur, beaucoup plus lentement.

## Utilisation

Placez vos vidéos dans `samples/` (ce dossier n'est jamais commité).

```sh
# Caractéristiques d'une vidéo (codec, résolution, fréquence, 8 ou 10 bits, audio)
uv run moto-blur probe samples/sortie.mp4

# Tout enchaîner : analyse, puis rendu flouté
uv run moto-blur run samples/sortie.mp4 -o samples/sortie_floutee.mp4

# La même chose en mode debug : cadres de détection et scores dessinés, pas de flou
uv run moto-blur run samples/sortie.mp4 -o samples/sortie_debug.mp4 --debug
```

Les deux passes peuvent être lancées séparément. L'analyse écrit un fichier projet
(`samples/sortie.motoblur.json`) ; le rendu le relit sans rien réanalyser :

```sh
uv run moto-blur analyze samples/sortie.mp4
uv run moto-blur render samples/sortie.motoblur.json -o samples/sortie_floutee.mp4
uv run moto-blur render samples/sortie.motoblur.json -o samples/sortie_debug.mp4 --debug
uv run moto-blur report samples/sortie.motoblur.json
```

### Montage

```sh
# Rogner 12,5 s au début et 8 s à la fin
uv run moto-blur run samples/a.mp4 -o samples/out.mp4 --trim-start 12.5 --trim-end 8

# Ne garder que deux passages (supprime ce qu'il y a entre les deux)
uv run moto-blur run samples/a.mp4 -o samples/out.mp4 --keep 0:10-1:30 --keep 2:00-3:15

# Assembler plusieurs clips, dans l'ordre donné
uv run moto-blur run samples/a.mp4 samples/b.mp4 -p samples/sortie.motoblur.json -o samples/out.mp4
```

Pour le reste (ordre des passages, passages muets, plusieurs passages sur plusieurs clips),
éditez le fichier projet. Les temps sont en secondes depuis le début de chaque clip source :

```json
"timeline": [
  { "clip": "c2", "start": 0.0,  "end": 42.0 },
  { "clip": "c1", "start": 10.0, "end": 95.5 }
],
"mutes": [
  { "clip": "c1", "start": 30.0, "end": 41.2 }
]
```

Puis relancez `analyze` (seules les parties jamais analysées le sont) et `render`.

Règles du montage :

- Les fichiers d'origine ne sont jamais modifiés ; le rendu refuse d'écraser une source.
- Les coupes sont précises à l'image près.
- Coupes et flous sont appliqués dans le même rendu : la vidéo n'est encodée qu'une fois.
- Sans aucune coupe (un clip entier, sans passage muet), l'audio est recopié sans réencodage.
  Sinon il est réencodé en AAC, avec un fondu de 8 ms à chaque coupe pour éviter les clics.
- Si les clips assemblés n'ont pas la même résolution, la même fréquence d'images ou la même
  profondeur de couleur, le rendu s'arrête et l'explique. Aucune conversion n'est faite en
  silence (et aucune n'est encore implémentée).

### Réglages utiles

| Option | Effet |
|---|---|
| `--device auto\|gpu\|cpu` | Accélération de l'analyse. `gpu` échoue au lieu de se replier sur le processeur. |
| `--face-model`, `--plate-model` | Modèle à utiliser (liste : `moto-blur info`, licences : `MODELS.md`). |
| `--only face\|plate` | N'analyser qu'une catégorie. |
| `--conf-new 0.20` | Score minimal pour commencer à suivre un objet. Plus bas : moins de fuites, plus de faux flous. |
| `--conf-keep 0.05` | Score minimal pour continuer à suivre un objet déjà vu. |
| `--tile-size N` | Côté des tuiles d'analyse, en pixels source. `0` = automatique (un tiers de la largeur), `-1` = pas de tuiles. Plus petit : meilleures chances sur les objets lointains, plus lent. |
| `--detect-every N` | Détecter une image sur N et interpoler entre les deux. Plus rapide ; plus risqué pour les objets qui n'apparaissent que brièvement. |
| `--margin 0.30` | Marge autour de chaque zone, en fraction de sa taille. |
| `--extend-s 0.30` | Durée de flou ajoutée avant l'apparition et après la disparition. |
| `--style pixelate\|solid` | Mosaïque grossière lissée, ou aplat de couleur. |
| `--quality 80` | Qualité d'encodage, de 0 à 100. |
| `--codec source\|h264\|hevc` | Codec de sortie. Par défaut celui de la source. |
| `--encoder auto\|hardware\|software\|NOM` | Encodeur. `auto` essaie le matériel (VideoToolbox sur Mac, AMF sur Radeon) puis se replie sur le logiciel. |

Les réglages d'analyse sont enregistrés dans le fichier projet. En changer un refait toute
l'analyse ; changer un réglage de flou ou de rendu ne refait rien.

Si l'analyse est trop lente, dans l'ordre du moins risqué au plus risqué : `--face-model
yolo11n-face` et `--plate-model yolo-v9-t-640`, puis `--tile-size` plus grand, puis
`--detect-every 2` (raisonnable à 60 i/s). `Ctrl+C` pendant l'analyse conserve ce qui est fait ;
relancer la même commande reprend là où elle s'est arrêtée.

## Comment ça marche

1. **Analyse.** Chaque image est découpée en tuiles qui se chevauchent, plus une passe sur
   l'image entière. Un détecteur de visages et un détecteur de plaques passent sur chaque
   tuile, avec un seuil de confiance bas. Un suivi relie les détections d'une image à l'autre
   en « pistes ». Le résultat est écrit dans le fichier projet, indexé sur le temps du fichier
   source : modifier le montage n'invalide jamais l'analyse.
2. **Zones de flou.** Pour chaque piste : interpolation dans les trous de détection,
   prolongation avant et après, union avec les images voisines, marge. Ces zones sont
   recalculées à chaque rendu à partir des pistes et des réglages de flou.
3. **Rendu.** Les passages gardés sont décodés dans l'ordre du montage, floutés et encodés une
   seule fois. Le flou remplace la zone par cinq cellules de couleur moyenne au plus,
   ré-agrandies en lissant : l'information d'origine est détruite, pas atténuée. Tout ce qui est
   hors des zones reste identique à la source avant encodage ; le 10 bits et les métadonnées
   de couleur sont conservés.
4. **Rapport.** Les pistes fragiles sont listées avec leurs horodatages : confiance faible,
   suivi perdu puis retrouvé, objet très petit, piste très courte, objet partiel au bord de
   l'image. Ces passages sont floutés comme les autres ; le rapport dit où regarder.

Le fichier projet est un JSON unique : clips, montage (`timeline`, `mutes`), réglages, pistes
(`analysis`) et passages à vérifier (`review`).

## Limites connues

- Les détecteurs n'ont pas été évalués sur vos vidéos. Le rappel réel sur petites plaques
  belges et françaises, de nuit ou sous la pluie, est inconnu tant que vous ne l'avez pas mesuré.
- Les occultations partielles ne sont pas détectées en tant que telles ; seul le cas « objet
  coupé par le bord de l'image » est signalé.
- Une vidéo portant une rotation dans ses métadonnées est analysée non tournée (avertissement).
- Seule la première piste audio est conservée.
- L'encodage matériel (VideoToolbox, AMF) et l'accélération CoreML et DirectML n'ont pas pu être
  testés pendant le développement, fait sur une machine Linux sans carte graphique.
- Avec `--codec h264` sur une source 10 bits, la sortie est en 8 bits.

## Développement

```sh
uv run pytest        # tests (aucun modèle ni carte graphique requis)
uv run ruff check src tests
```

Les tests couvrent le suivi, l'interpolation, la prolongation et les marges des zones, la
conversion entre temps source et temps du montage, l'analyse incrémentale, et un rendu complet
sur une vidéo de synthèse (coupes à l'image près, audio sans clic, flou effectif).

## Licence

AGPL-3.0-or-later. Les modèles téléchargés ont leurs propres licences : voir
[MODELS.md](MODELS.md).
