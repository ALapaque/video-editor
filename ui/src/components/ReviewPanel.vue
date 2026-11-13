<script setup lang="ts">
// Onglet « Vérification » : écran de choix, passages un par un, relecture complète, bilan.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { Track } from "../api";
import { blurSpan, confidence, confidenceInfo, thumbUrl } from "../extras";
import { byDoubt, displayName, splitReasons } from "../blurs";
import {
  addMark, currentEntry, currentIndex, dismiss, doubtDone, doubtEntries, doubtLeft, droppedInPassage, entries, explain, extra, finish, forceZone, leave,
  markEntries, missing, next, objectHere, openSheet, previous, relocate, removeZone, replay, restoreZone, resume, resumeSweep, review, sheetItems, showObject, soloTrack, speeds,
  startQueue, startSweep, sweepResume, unvalidate, validate,
} from "../review";
import { busy, droppedHere, duration, formatTime, kindLabel, needsAnalysis, objectNames, player, project, state, time, trackById } from "../store";
import BlurRow from "./BlurRow.vue";
import ContactSheet from "./ContactSheet.vue";
import Icon from "./Icon.vue";

const entry = currentEntry;
/** Vignette de l'objet, quand l'élément affiché ne porte que sur un seul. */
const soloThumb = computed(() => (entry.value && soloTrack.value ? thumbUrl(entry.value.clip, soloTrack.value.id) : ""));
const thumbFailed = ref(""); // adresse de la vignette qui n'a pas pu être chargée
const sweepDone = computed(() => project.value?.sweep?.done === true);
/** Passage chargé : nombre de ses flous automatiques, quand il réunit plus de 8 objets (sinon 0). */
const crowd = computed(() => {
  const item = entry.value?.item;
  if (!item || item.tracks.length <= 8) return 0;
  const ids = new Set(item.tracks);
  return sheetItems.value.filter((i) => i.clip === item.clip && ids.has(i.track.id)).length;
});
const total = computed(() => entries.value.length);
const progress = computed(() => {
  if (review.scope === "marks") return 0;
  return doubtEntries.value.length ? doubtDone.value / doubtEntries.value.length : 0;
});
/** Flous du passage affiché (8 au plus ; au-delà, la planche les montre tous), le plus douteux d'abord. */
const blurs = computed<Track[]>(() => {
  const item = entry.value?.item;
  if (!item || item.tracks.length > 8) return [];
  return item.tracks.map((id) => trackById(item.clip, id)).filter((t): t is Track => !!t && !t.disabled).sort(byDoubt);
});
/** Raisons du passage : rattachées au flou concerné quand on peut le savoir, les autres une seule fois en dessous. */
const split = computed(() => {
  const item = entry.value?.item;
  if (!item) return { byTrack: new Map<number, string[]>(), rest: [] as string[] };
  if (soloTrack.value || !blurs.value.length) return { byTrack: new Map<number, string[]>(), rest: item.reasons };
  return splitReasons(item, blurs.value);
});
const reasons = computed(() => explain(split.value.rest));
function reasonsOf(track: Track): { text: string; raw: string }[] {
  return explain(split.value.byTrack.get(track.id) ?? []);
}

// Survoler ou sélectionner un flou de la liste le fait ressortir sur la vidéo (le reste est assombri).
const pinned = ref<number | null>(null);
function focusOn(id: number | null): void {
  const e = entry.value;
  const target = id ?? pinned.value;
  state.focus = e && target !== null ? { clip: e.clip, ids: [target] } : null;
}
function pin(track: Track): void {
  pinned.value = pinned.value === track.id ? null : track.id;
  focusOn(null);
}
watch(() => [review.current, review.mode, review.sub], () => {
  pinned.value = null;
  state.focus = null;
});
onBeforeUnmount(() => (state.focus = null));
/** Étiquettes du passage : catégories détectées, et nom de chaque objet désigné à la main. */
const tags = computed(() => {
  const item = entry.value?.item;
  if (!item) return [];
  const out: { kind: string; label: string }[] = [];
  for (const k of item.kinds) {
    if (k !== "dash") out.push({ kind: k, label: kindLabel[k] ?? k });
  }
  const names = new Set(item.tracks.map((id) => objectNames.value.get(`${item.clip}:${id}`)).filter((n): n is string => !!n));
  for (const n of names) out.push({ kind: "dash", label: n });
  return out;
});
const sweepEnded = computed(() => !state.playing && time.value >= duration.value - 0.2);

function plural(n: number, one: string, many: string): string {
  return `${n} ${n > 1 ? many : one}`;
}
function toExport(): void {
  leave();
  state.step = 4;
}
// Après un clic à la souris, le bouton ne garde pas le focus : Entrée valide toujours le passage
// affiché au lieu de répéter le dernier bouton cliqué. Au clavier, le focus reste où il est.
function releaseFocus(event: MouseEvent): void {
  if (event.detail > 0 && document.activeElement instanceof HTMLButtonElement) document.activeElement.blur();
}
function stopSweep(): void {
  player.pause();
}
</script>

<template>
  <section class="pane review-pane" @click="releaseFocus">
    <!-- Écran de choix -->
    <template v-if="review.mode === 'home'">
      <h4>Vérifier le floutage</h4>
      <p class="help">Avant d'exporter, regardez ce que le logiciel a flouté. Deux façons de faire, à enchaîner dans cet ordre.</p>
      <p v-if="needsAnalysis && !busy" class="note warn"><Icon name="alert" />L'analyse n'est pas à jour : lancez-la d'abord, sinon des passages sortiraient sans flou.</p>
      <div class="choices">
        <button class="choice" :class="{ suggested: doubtLeft > 0 }" :disabled="busy || !doubtEntries.length" @click="startQueue('doubt')">
          <span>
            <span class="choice-title">Passages douteux <b class="count">{{ doubtLeft }}</b></span>
            <small v-if="doubtEntries.length">Le logiciel vous montre un par un les endroits où il a hésité.{{ doubtDone ? ` ${plural(doubtDone, "passage déjà validé", "passages déjà validés")} sur ${doubtEntries.length}.` : "" }}</small>
            <small v-else>Le logiciel n'a hésité nulle part dans ce montage.</small>
          </span>
        </button>
        <button class="choice" :class="{ suggested: doubtLeft === 0 && !markEntries.length && !sweepDone }" :disabled="busy" @click="resumeSweep()">
          <span>
            <span class="choice-title">Relecture complète <span v-if="sweepDone" class="tag ok"><Icon name="check" :size="12" />faite</span></span>
            <small v-if="sweepResume !== null">Vous vous étiez arrêté à {{ formatTime(Math.floor(sweepResume), 0) }} sur {{ formatTime(duration, 0) }}. Vous regardez le montage avec le flou affiché et vous signalez d'une touche ce qui n'est pas flouté.</small>
            <small v-else>Vous regardez tout le montage avec le flou affiché et vous signalez d'une touche ce qui n'est pas flouté. C'est le seul moyen de repérer ce que le logiciel n'a jamais détecté.</small>
          </span>
        </button>
        <div v-if="sweepResume !== null" class="row pair sweep-resume">
          <button class="on" :disabled="busy" @click="resumeSweep()"><Icon name="play" :size="12" />Reprendre à {{ formatTime(Math.floor(sweepResume), 0) }}</button>
          <button :disabled="busy" @click="startSweep(true)">Depuis le début</button>
        </div>
        <button v-if="markEntries.length" class="choice" :class="{ suggested: doubtLeft === 0 }" :disabled="busy" @click="startQueue('marks')">
          <span>
            <span class="choice-title">Signalements à traiter <b class="count">{{ markEntries.length }}</b></span>
            <small>Les endroits que vous avez signalés pendant la relecture, un par un.</small>
          </span>
        </button>
        <button class="choice" :disabled="!sheetItems.length" @click="openSheet()">
          <span>
            <span class="choice-title"><Icon name="grid" />Planche des flous <b class="count">{{ sheetItems.length }}</b></span>
            <small v-if="sheetItems.length">Tous les flous du logiciel en vignettes, les plus douteux d'abord. Cochez les faux flous et retirez-les d'un coup.</small>
            <small v-else>Aucun flou automatique dans ce montage pour l'instant.</small>
          </span>
        </button>
      </div>
      <p class="note"><Icon name="info" />Le logiciel ne signale que ce qu'il a détecté : pour ne rien laisser passer, faites aussi la relecture complète.</p>
    </template>

    <!-- Un passage à la fois -->
    <template v-else-if="review.mode === 'queue'">
      <div class="guided-head">
        <h4>{{ review.scope === "doubt" ? "Passages douteux" : "Signalements" }}</h4>
        <button class="quiet small" title="Revenir au choix du mode de vérification" @click="leave"><Icon name="close" :size="14" />Quitter</button>
      </div>
      <div v-if="review.scope === 'doubt'" class="progress" :title="`${doubtDone} validés sur ${doubtEntries.length}`">
        <span class="bar still"><span :style="{ transform: `scaleX(${progress})` }"></span></span>
        <span class="progress-text"><span class="mono">{{ doubtDone }} / {{ doubtEntries.length }}</span> validés</span>
      </div>

      <div v-if="entry" class="card" :class="{ done: entry.done }">
        <div class="card-top">
          <strong class="card-pos">{{ entry.type === "doubt" ? "Passage" : "Signalement" }} {{ currentIndex + 1 }} sur {{ total }}</strong>
          <span v-if="entry.done" class="tag ok"><Icon name="check" :size="12" />validé</span>
        </div>
        <div class="card-when">
          <span class="mono">{{ formatTime(entry.at, 1) }}<template v-if="entry.end > entry.at"> → {{ formatTime(entry.end, 1) }}</template></span>
          <span v-for="t in tags" :key="t.kind + t.label" class="tag"><span class="dot" :class="t.kind"></span>{{ t.label }}</span>
        </div>
        <!-- élément qui ne porte que sur un objet : on le montre, pour savoir de quoi il est question -->
        <div v-if="soloTrack" class="card-object" @mouseenter="focusOn(soloTrack.id)" @mouseleave="focusOn(null)">
          <img v-if="soloThumb && thumbFailed !== soloThumb" :src="soloThumb" alt="" width="220" height="165" @error="thumbFailed = soloThumb" />
          <span class="grow">
            <strong>{{ displayName(soloTrack) }} · <span class="level" :class="confidence(soloTrack)" :title="confidenceInfo[confidence(soloTrack)].help">{{ confidenceInfo[confidence(soloTrack)].label }}</span></strong>
            <small>{{ blurSpan(soloTrack) }} dans ce clip</small>
            <small>La question porte sur ce seul flou, encadré ici là où il est le plus net.</small>
          </span>
        </div>
        <!-- quelques flous : un par ligne, le plus douteux d'abord, avec ce qui le rend douteux -->
        <template v-else-if="blurs.length">
          <p class="blur-list-head">{{ blurs.length > 1 ? `${blurs.length} flous dans ce passage, le plus douteux d'abord. Survolez-en un pour le voir sur la vidéo.` : "Le flou de ce passage :" }}</p>
          <ul class="blur-list" @mouseleave="focusOn(null)">
            <li v-for="t in blurs" :key="t.id" :data-id="t.id" :class="{ pinned: pinned === t.id }">
              <button
                class="blur-pick" :aria-pressed="pinned === t.id" :title="pinned === t.id ? 'Ne plus faire ressortir ce flou' : 'Faire ressortir ce flou sur la vidéo'"
                @mouseenter="focusOn(t.id)" @focus="focusOn(t.id)" @blur="focusOn(null)" @click="pin(t)"
              >
                <BlurRow :clip="entry.clip" :track="t" />
              </button>
              <ul v-if="reasonsOf(t).length" class="why small">
                <li v-for="r in reasonsOf(t)" :key="r.text" :title="`Détail technique : ${r.raw}`">{{ r.text }}</li>
              </ul>
              <button
                v-if="entry.type === 'doubt' && review.sub === 'ask'" class="icon-btn bare blur-remove" :disabled="busy"
                :title="`Faux flou : retirer « ${displayName(t)} » (vous pourrez le rétablir)`" :aria-label="`Retirer le flou ${displayName(t)}`" @click="removeZone(entry.clip, t.id)"
              ><Icon name="close" :size="14" /></button>
            </li>
          </ul>
        </template>
        <ul v-if="reasons.length" class="why">
          <li v-for="r in reasons" :key="r.text" :title="`Détail technique : ${r.raw}`">{{ r.text }}</li>
        </ul>
        <p v-else-if="!entry.item" class="why-text">Vous avez signalé cet endroit pendant la relecture.</p>
        <!-- passage chargé (foule) : la planche, limitée à ses flous, permet de les trier d'un coup -->
        <button v-if="crowd" class="quiet small wrap sheet-link" :disabled="busy" @click="openSheet({ clip: entry.clip, ids: entry.item!.tracks })">
          <Icon name="grid" />Beaucoup de flous ici ({{ crowd }}) : triez-les d'un coup avec la planche
        </button>

        <div class="loop-row">
          <span class="dim">Le passage tourne en boucle, flou affiché.</span>
          <button class="small" title="Reprendre la boucle au début du passage" @click="replay"><Icon name="play" :size="12" />Rejouer</button>
        </div>
        <div class="speed" role="group" aria-label="Vitesse de l'aperçu">
          <span class="dim">Vitesse</span>
          <span class="joined">
            <button v-for="s in speeds" :key="s.value" class="small" :class="{ on: review.speed === s.value }" :aria-pressed="review.speed === s.value" @click="review.speed = s.value">{{ s.label }}</button>
          </span>
        </div>

        <template v-if="review.sub === 'ask'">
          <p class="question">Tout ce qui doit être flouté l'est-il ?</p>
          <button class="primary wide" :disabled="busy" @click="validate">
            <Icon name="check" />{{ entry.type === "doubt" ? "Oui, c'est bon" : "C'est réglé" }}<kbd>Entrée</kbd>
          </button>
          <div class="row pair">
            <button :disabled="busy" title="Tracer un flou là où il en manque un" @click="missing">Il manque un flou</button>
            <button :disabled="busy" title="Retirer un flou posé à tort" @click="extra">Un flou est en trop</button>
          </div>
          <button v-if="soloTrack?.source === 'auto' && entry.type === 'doubt'" class="wide" :disabled="busy" :title="`Faux flou : retirer « ${displayName(soloTrack)} », et lui seul, puis passer au suivant`" @click="dismiss">
            <Icon name="close" />Ce n'est pas à flouter
          </button>
          <button v-if="objectHere" class="wide" :disabled="busy" title="Entourer l'objet sur cette image : son suivi est refait sur ce plan seulement" @click="showObject">
            <Icon name="target" />Montrer « {{ objectHere }} » ici
          </button>
        </template>
        <div v-else-if="review.sub === 'object'" class="substate">
          <strong>Montrer « {{ objectHere }} »</strong>
          <p>Tracez un rectangle autour de « {{ objectHere }} » sur l'image affichée. Son suivi sera refait sur ce plan, jusqu'au prochain changement de plan ; le reste est conservé.</p>
          <button class="wide" @click="resume">Annuler</button>
        </div>
        <div v-else-if="review.sub === 'missing'" class="substate">
          <strong>Il manque un flou</strong>
          <p>Tracez un rectangle sur l'image, à l'endroit à flouter. Il couvrira tout le passage, puis la boucle reprendra.</p>
          <template v-if="droppedInPassage">
            <button v-if="!state.showDropped" class="wide wrap" @click="state.showDropped = true">Le logiciel a vu ici {{ droppedInPassage }} {{ droppedInPassage > 1 ? "objets trop peu sûrs pour les flouter" : "objet trop peu sûr pour le flouter" }} : les afficher</button>
            <template v-else>
              <p>Ils sont entourés en gris. Si l'un d'eux couvre ce qui manque, floutez-le :</p>
              <p v-if="!droppedHere.length" class="dim">Aucune sur cette image. Avancez image par image (touche →) pour en voir d'autres.</p>
              <ul class="list dropped">
                <li v-for="tr in droppedHere" :key="tr.id" :data-id="tr.id">
                  <BlurRow class="grow" :clip="entry.clip" :track="tr" />
                  <button class="small" :disabled="busy" @click="forceZone(entry.clip, tr.id)">Flouter quand même</button>
                </li>
              </ul>
            </template>
          </template>
          <button @click="resume">Annuler</button>
        </div>
        <div v-else class="substate">
          <strong>Un flou est en trop</strong>
          <p>Cliquez le flou à retirer dans l'image. Vous pourrez le rétablir juste après.</p>
          <button @click="resume">Annuler</button>
        </div>

        <ul v-if="review.removed.length" class="removed">
          <li v-for="r in review.removed" :key="`${r.clip}:${r.id}`">
            <span class="grow">Flou retiré : {{ r.label }}</span>
            <button class="small" :disabled="busy" @click="restoreZone(r)">Rétablir</button>
          </li>
        </ul>
        <button v-if="entry.done && review.sub === 'ask'" class="quiet small left" :disabled="busy" @click="unvalidate">Remettre ce passage à revoir</button>
      </div>
      <div v-else class="card">
        <p class="why-text">Ce passage n'est plus dans la liste : il a changé ou il a été coupé du montage.</p>
        <button class="primary wide" @click="relocate">Continuer</button>
      </div>

      <div class="row pair nav">
        <button :disabled="currentIndex <= 0" @click="previous"><Icon name="chevronLeft" />Précédent</button>
        <button title="Passer au suivant sans valider celui-ci" @click="next">Plus tard<Icon name="chevronRight" /></button>
      </div>
    </template>

    <!-- Relecture complète -->
    <template v-else-if="review.mode === 'sweep'">
      <div class="guided-head">
        <h4>Relecture complète</h4>
        <button class="quiet small" title="Revenir au choix du mode de vérification" @click="leave"><Icon name="close" :size="14" />Quitter</button>
      </div>
      <p class="help">Regardez le montage, flou affiché. Dès qu'un visage, une plaque ou le compteur reste lisible, signalez-le : la lecture continue, vous corrigerez à la fin.</p>
      <div class="progress">
        <span class="bar still"><span :style="{ transform: `scaleX(${duration ? Math.min(1, time / duration) : 0})` }"></span></span>
        <span class="progress-text mono">{{ formatTime(time, 0) }} / {{ formatTime(duration, 0) }}</span>
      </div>
      <button class="primary signal" :disabled="busy" @click="addMark"><Icon name="flag" :size="18" />Signaler ici<kbd>S</kbd></button>
      <p class="flash" :class="{ shown: !!review.flash }" role="status">{{ review.flash || " " }}</p>
      <div class="speed" role="group" aria-label="Vitesse de l'aperçu">
        <span class="dim">Vitesse</span>
        <span class="joined">
          <button v-for="s in speeds" :key="s.value" class="small" :class="{ on: review.speed === s.value }" :aria-pressed="review.speed === s.value" @click="review.speed = s.value">{{ s.label }}</button>
        </span>
      </div>
      <div class="card">
        <p v-if="state.playing" class="why-text">Lecture en cours. {{ plural(markEntries.length, "signalement", "signalements") }} pour l'instant.</p>
        <p v-else-if="sweepEnded" class="why-text"><strong>Relecture terminée.</strong> {{ markEntries.length ? plural(markEntries.length, "signalement à traiter", "signalements à traiter") + "." : "Vous n'avez rien signalé." }}</p>
        <p v-else class="why-text">Relecture en pause à <span class="mono">{{ formatTime(time, 1) }}</span>. {{ plural(markEntries.length, "signalement", "signalements") }}.</p>
        <button v-if="state.playing" class="wide" @click="stopSweep"><Icon name="pause" />Arrêter la relecture</button>
        <template v-else>
          <button v-if="markEntries.length" class="primary wide" @click="startQueue('marks')">Traiter {{ plural(markEntries.length, "le signalement", "les signalements") }}</button>
          <button v-if="!sweepEnded" class="wide" @click="startSweep()"><Icon name="play" :size="12" />Reprendre la relecture</button>
          <button v-else class="wide" @click="startSweep(true)">Relire depuis le début</button>
          <button v-if="!markEntries.length" class="wide" :class="{ primary: sweepEnded }" @click="finish">Terminer</button>
        </template>
      </div>
    </template>

    <!-- Bilan -->
    <template v-else>
      <h4>Vérification terminée</h4>
      <div class="card result">
        <p class="result-line"><Icon name="check" :size="18" /><span><strong>Vérification terminée :</strong> {{ plural(doubtDone, "passage validé", "passages validés") }}, {{ doubtLeft }} à revoir.</span></p>
        <p v-if="markEntries.length" class="why-text">{{ plural(markEntries.length, "signalement", "signalements") }} de relecture à traiter.</p>
        <button v-if="doubtLeft" class="wide" :class="{ primary: doubtLeft > 0 }" @click="startQueue('doubt')">Revoir les passages restants</button>
        <button v-if="markEntries.length" class="wide" :class="{ primary: !doubtLeft }" @click="startQueue('marks')">Traiter {{ plural(markEntries.length, "le signalement", "les signalements") }}</button>
        <button v-if="sweepResume !== null" class="wide" @click="resumeSweep()">Reprendre la relecture à {{ formatTime(Math.floor(sweepResume), 0) }}</button>
        <button class="wide" @click="startSweep(true)">{{ sweepResume !== null ? "Relecture complète depuis le début" : "Relecture complète" }}</button>
        <button v-if="sheetItems.length" class="wide" @click="openSheet()"><Icon name="grid" />Planche des flous</button>
        <button class="wide" :class="{ primary: !doubtLeft && !markEntries.length }" @click="toExport">Passer à l'export</button>
      </div>
      <p class="note"><Icon name="info" />Le logiciel ne signale que ce qu'il a détecté : pour ne rien laisser passer, faites aussi la relecture complète.</p>
    </template>
    <Teleport to="body">
      <Transition name="modal">
        <ContactSheet v-if="review.sheet" />
      </Transition>
    </Teleport>
  </section>
</template>

<style>
/* === 12 points : vérification objet par objet === */
.blur-list-head { color: var(--dim); font-size: 12px; line-height: 1.45; }
.blur-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.blur-list > li { position: relative; display: flex; flex-direction: column; gap: 4px; padding: 4px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; transition: border-color var(--m-out) var(--ease); }
.blur-list > li:hover, .blur-list > li.pinned { border-color: var(--line-strong); }
.blur-list > li.pinned { border-color: rgba(255, 255, 255, .72); }
.blur-pick { width: 100%; height: auto; justify-content: flex-start; padding: 2px 30px 2px 2px; text-align: left; white-space: normal; font-weight: 400; background: transparent; border-color: transparent; }
.blur-pick:hover:not(:disabled) { background: transparent; border-color: transparent; }
.blur-pick:active:not(:disabled) { transform: none; background: transparent; }
.blur-remove { position: absolute; top: 6px; right: 5px; }
.why.small { margin: 0 4px 2px 70px; padding-left: 12px; gap: 1px; color: var(--dim); font-size: 12px; line-height: 1.4; }
.card-object .level { font-weight: 600; }
</style>
