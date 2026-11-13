<script lang="ts">
// Planche des flous, en vignettes. Trois vues :
//  - « Flous gênants » (par défaut) : les flous qui se voient, gros ou longs, les plus gênants
//    d'abord ; un interrupteur montre tous les flous du logiciel ;
//  - « Non floutés » : ce que le logiciel a vu sans le flouter (trop peu sûr) — cocher puis valider
//    floute quand même.
// Un clic coche ou décoche ; un bouton applique à tout ce qui est coché, en une seule action
// (qui s'annule avec Ctrl+Z). Après un retrait, les flous au même endroit sont proposés.
import { reactive } from "vue";

/** Flous cochés (« clip:numéro ») : gardés quand la planche se ferme pour aller voir un flou dans la vidéo. */
const checked = reactive(new Set<string>());
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { call } from "../api";
import { confidence, confidenceInfo, setTracksDisabled, thumbUrl } from "../extras";
import { displayName } from "../blurs";
import { almostItems, annoyingItems, closeSheet, review, sheetItems, type SheetItem } from "../review";
import { busy, formatTime, notify, player, seekTime, send, state } from "../store";
import Icon from "./Icon.vue";

const box = ref<HTMLDivElement>();
let opener: Element | null = null;

const almost = computed(() => review.sheetMode === "almost");
/** Interrupteur « Tous les flous » (hors vue « Non floutés »). */
const all = computed({
  get: () => review.sheetMode === "all",
  set: (on: boolean) => {
    review.sheetMode = on ? "all" : "annoying";
    review.sheetFilter = null;
    last = -1;
  },
});
const pool = computed(() => (almost.value ? almostItems.value : review.sheetMode === "all" ? sheetItems.value : annoyingItems.value));
/** Flous montrés : ceux de la vue, ou seulement ceux du filtre (passage chargé, flous au même endroit). */
const items = computed(() => {
  const filter = review.sheetFilter;
  if (!filter) return pool.value;
  const ids = new Set(filter.ids);
  return pool.value.filter((i) => i.clip === filter.clip && ids.has(i.track.id));
});
function showAll(): void {
  review.sheetFilter = null;
  last = -1;
}
function setMode(mode: "annoying" | "almost"): void {
  if ((mode === "almost") === almost.value) return;
  review.sheetMode = mode;
  review.sheetFilter = null;
  offer.value = null;
  last = -1;
}
const selected = computed(() => items.value.filter((i) => checked.has(i.key)));
const count = computed(() => selected.value.length);
const applyLabel = computed(() => {
  if (almost.value) return count.value <= 1 ? "Flouter quand même" : `Flouter quand même les ${count.value} cochés`;
  return count.value === 0 ? "Retirer les flous cochés" : count.value === 1 ? "Retirer le flou coché" : `Retirer les ${count.value} flous cochés`;
});
const intro = computed(() => {
  if (almost.value) return "Ce que le logiciel a cru voir sans le flouter, car trop peu sûr, le plus probable d'abord. Un vrai visage ou une vraie plaque peut s'y cacher : cochez ce qui doit être flouté, puis validez.";
  if (review.sheetFilter) return "Seuls quelques flous sont affichés. Cliquez ceux qui sont en trop pour les cocher, puis retirez-les d'un coup.";
  if (all.value) return "Tous les flous posés par le logiciel, les plus gênants d'abord. Cliquez ceux qui sont en trop (reflet, feuillage, panneau…) pour les cocher, puis retirez-les d'un coup. Maj + clic coche toute une série.";
  return "Les flous qui se voient le plus, gros ou longs, les plus gênants d'abord. Rien n'est obligatoire : un flou en trop ne fait courir aucun risque. Cliquez ceux qui sont en trop pour les cocher, puis retirez-les d'un coup.";
});
const emptyText = computed(() => {
  if (almost.value) return "Rien : le logiciel a flouté tout ce qu'il a vu.";
  if (review.sheetFilter) return "Plus aucun flou à cet endroit.";
  return all.value ? "Aucun flou posé par le logiciel dans ce montage." : "Aucun flou gênant : aucun ne prend beaucoup de place à l'écran.";
});

// Adresse de chaque vignette, fixée à la première demande : retirer des flous ne fait pas
// recharger les vignettes de ceux qui restent.
const urls = new Map<string, string>();
function thumb(item: SheetItem): string {
  let url = urls.get(item.key);
  if (!url) urls.set(item.key, (url = thumbUrl(item.clip, item.track.id)));
  return url;
}
const failed = reactive(new Set<string>());

// Une vignette n'est demandée au moteur que lorsqu'elle approche de l'écran (chacune lui coûte
// la lecture d'une image de la vidéo) : avec des centaines de flous, la planche s'ouvre aussitôt.
const seen = reactive(new Set<string>());
let watcher: IntersectionObserver | null = null;
const keys = new WeakMap<Element, string>();
const vSeen = {
  mounted(el: HTMLElement, binding: { value: string }): void {
    watcher ??= new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const key = keys.get(entry.target);
        if (key) seen.add(key);
        watcher?.unobserve(entry.target);
      }
    }, { root: el.closest(".sheet-grid"), rootMargin: "400px 0px" });
    keys.set(el, binding.value);
    watcher.observe(el);
  },
  unmounted(el: HTMLElement): void {
    watcher?.unobserve(el);
  },
};

function level(item: SheetItem): string {
  return confidenceInfo[confidence(item.track)].label;
}
/** « prend 3 % de l'image pendant 4 s » : ce qui rend un flou gênant, en clair. */
function footprint(item: SheetItem): string {
  const t = item.track;
  const area = t.area ?? 0;
  const size = area >= 10 ? `${Math.round(area)} %` : area >= 1 ? `${String(Math.round(area * 10) / 10).replace(".", ",")} %` : "moins de 1 %";
  const seconds = Math.max(0, t.end - t.start);
  const time = seconds < 1 ? "moins d'une seconde" : `${Math.round(seconds)} s`;
  return `prend ${size} de l'image pendant ${time}`;
}
function label(item: SheetItem): string {
  return almost.value
    ? `${displayName(item.track)}, non flouté, à ${formatTime(item.at, 0)}`
    : `${displayName(item.track)}, ${level(item)}, ${footprint(item)}, à ${formatTime(item.at, 0)}`;
}

let last = -1; // dernière vignette cliquée, pour cocher une série avec Maj
function toggle(index: number, event: MouseEvent): void {
  const on = !checked.has(items.value[index].key);
  const from = event.shiftKey && last >= 0 ? Math.min(last, index) : index;
  const to = event.shiftKey && last >= 0 ? Math.max(last, index) : index;
  for (let i = from; i <= to && i < items.value.length; i++) {
    if (on) checked.add(items.value[i].key);
    else checked.delete(items.value[i].key);
  }
  last = index;
}
function uncheckAll(): void {
  for (const item of items.value) checked.delete(item.key);
}

function byClip(list: SheetItem[]): Map<string, number[]> {
  const out = new Map<string, number[]>();
  for (const item of list) out.set(item.clip, [...(out.get(item.clip) ?? []), item.track.id]);
  return out;
}
/** Les cases de ce qui n'est plus proposé (retiré, flouté) sont oubliées ; renvoie combien ont été traitées. */
function forget(before: number): number {
  const still = new Set([...sheetItems.value, ...almostItems.value].map((i) => i.key));
  for (const key of [...checked]) if (!still.has(key)) checked.delete(key);
  last = -1;
  return before - count.value;
}

// Après un retrait : les flous encore en place au même endroit que ceux qu'on vient de retirer
// (la même fausse détection qui revient). Proposés, jamais retirés d'office.
const offer = ref<{ clip: string; ids: number[] } | null>(null);
async function findSimilar(removed: Map<string, number[]>): Promise<void> {
  offer.value = null;
  let best: { clip: string; ids: number[] } | null = null;
  for (const [clip, ids] of removed) {
    const found = new Set<number>();
    for (const id of ids.slice(0, 12)) {
      try {
        for (const other of (await call<{ ids: number[] }>("GET", `/api/similar?clip=${encodeURIComponent(clip)}&id=${id}`)).ids) found.add(other);
      } catch {
        /* sans gravité : pas de proposition */
      }
    }
    const here = new Set(sheetItems.value.filter((i) => i.clip === clip).map((i) => i.track.id)); // encore en place, dans le montage
    const left = [...found].filter((id) => here.has(id));
    if (left.length && (!best || left.length > best.ids.length)) best = { clip, ids: left };
  }
  if (review.sheet && !almost.value) offer.value = best;
}
async function removeOffered(): Promise<void> {
  const o = offer.value;
  offer.value = null;
  if (!o) return;
  await setTracksDisabled(o.clip, o.ids, true);
  if (review.sheetFilter) review.sheetFilter = null;
  forget(count.value);
  notify(`${o.ids.length > 1 ? `${o.ids.length} flous retirés` : "1 flou retiré"} au même endroit. Pour ${o.ids.length > 1 ? "les" : "le"} remettre : « Annuler » (Ctrl+Z).`);
}
/** « Les voir » : la planche ne montre plus que ces flous, déjà cochés. */
function showOffered(): void {
  const o = offer.value;
  if (!o) return;
  review.sheetMode = "all";
  review.sheetFilter = { clip: o.clip, ids: o.ids };
  for (const id of o.ids) checked.add(`${o.clip}:${id}`);
  offer.value = null;
  last = -1;
}

async function applyChecked(): Promise<void> {
  const before = count.value;
  if (!before) return;
  const groups = byClip(selected.value);
  if (almost.value) {
    for (const [clip, ids] of groups) await send("PATCH", "/api/tracks", { clip, ids, forced: true });
    const n = forget(before);
    if (n) notify(`${n > 1 ? `${n} objets sont maintenant floutés` : "1 objet est maintenant flouté"}. Pour revenir en arrière : « Annuler » (Ctrl+Z).`);
    return;
  }
  for (const [clip, ids] of groups) await setTracksDisabled(clip, ids, true);
  // ce qui est encore dans la planche n'a pas pu être retiré (le moteur a dit pourquoi) : ça reste coché
  const n = forget(before);
  if (n) notify(`${n > 1 ? `${n} flous retirés` : "1 flou retiré"}. Pour ${n > 1 ? "les" : "le"} remettre : « Annuler » (Ctrl+Z).`);
  if (n) void findSimilar(groups);
}

/** Aller voir un flou dans la vidéo : la planche se ferme, les cases cochées sont gardées. */
function see(item: SheetItem): void {
  review.sheet = false; // sans relancer la boucle de vérification : on regarde ce flou, à l'arrêt
  review.sheetFilter = null;
  player.pause();
  seekTime(item.at);
  state.preview = "boxes"; // le flou se juge sur l'image nette, entourée (contours)
  if (almost.value) {
    // un objet non flouté est entouré en gris, et listé dans l'onglet « Flous »
    state.showDropped = true;
    state.tab = "zones";
    notify(`${displayName(item.track)} non flouté : il est entouré de pointillés gris dans l'image, et listé à droite sous « Non floutés ». Rouvrez la planche pour continuer${checked.size ? ", vos cases cochées sont gardées" : ""}.`);
    return;
  }
  state.selectedTrack = { clip: item.clip, id: item.track.id };
  notify(`${displayName(item.track)} : il est entouré d'un trait épais dans l'image. Rouvrez la planche pour continuer${checked.size ? ", vos cases cochées sont gardées" : ""}.`);
}

function close(): void {
  closeSheet();
}

function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape" && !state.jobOpen) {
    event.stopPropagation();
    close();
  }
}
watch(() => review.sheetMode, () => void nextTick(() => box.value?.querySelector(".sheet-grid")?.scrollTo({ top: 0 })));
onMounted(() => {
  opener = document.activeElement;
  // les cases de flous qui n'existent plus (retirés ailleurs, nouvelle analyse) sont oubliées
  forget(0);
  window.addEventListener("keydown", onKey, true);
  void nextTick(() => box.value?.focus());
});
onBeforeUnmount(() => {
  watcher?.disconnect();
  window.removeEventListener("keydown", onKey, true);
  if (opener instanceof HTMLElement && document.contains(opener)) opener.focus({ preventScroll: true });
});
</script>

<template>
  <div class="modal" @pointerdown.self="close">
    <div ref="box" class="dialog sheet" :class="{ almost }" role="dialog" aria-modal="true" aria-labelledby="sheet-title" tabindex="-1">
      <div class="sheet-head">
        <h3 id="sheet-title"><Icon :name="almost ? 'eye' : 'grid'" :size="18" />{{ almost ? "Non floutés à regarder" : all || review.sheetFilter ? "Planche des flous" : "Flous gênants" }}</h3>
        <span class="joined sheet-modes" role="group" aria-label="Ce que montre la planche">
          <button class="small" :class="{ on: !almost }" :aria-pressed="!almost" @click="setMode('annoying')">Flous<span class="mono kind-count">{{ all ? sheetItems.length : annoyingItems.length }}</span></button>
          <button class="small" :class="{ on: almost }" :aria-pressed="almost" :disabled="!almostItems.length && !almost" @click="setMode('almost')">Non floutés<span class="mono kind-count">{{ almostItems.length }}</span></button>
        </span>
        <span class="grow"></span>
        <label v-if="!almost" class="check sheet-all" :title="`Montrer aussi les petits flous brefs, qui ne se remarquent pas (${sheetItems.length} flous en tout)`">
          <input v-model="all" type="checkbox" /> Tous les flous ({{ sheetItems.length }})
        </label>
        <button class="icon-btn bare" aria-label="Fermer la planche" title="Fermer (Échap)" @click="close"><Icon name="close" /></button>
      </div>
      <p>{{ intro }}</p>
      <p v-if="review.sheetFilter" class="sheet-scope">
        <span class="grow">Seuls {{ items.length > 1 ? `ces ${items.length} flous` : "quelques flous" }} sont affichés.</span>
        <button class="small" @click="showAll">Voir tous les flous du montage ({{ sheetItems.length }})</button>
      </p>
      <p v-if="offer" class="sheet-scope sheet-offer" role="status">
        <Icon name="info" />
        <span class="grow">{{ offer.ids.length > 1 ? `${offer.ids.length} autres flous sont au même endroit` : "1 autre flou est au même endroit" }} que {{ offer.ids.length > 1 ? "ceux" : "ce" }} que vous venez de retirer.</span>
        <button class="small" @click="showOffered">{{ offer.ids.length > 1 ? "Les voir" : "Le voir" }}</button>
        <button class="small attention" :disabled="busy" @click="removeOffered">{{ offer.ids.length > 1 ? `Retirer aussi ces ${offer.ids.length} flous` : "Le retirer aussi" }}</button>
        <button class="icon-btn bare" aria-label="Ne rien retirer de plus" title="Ne rien retirer de plus" @click="offer = null"><Icon name="close" :size="14" /></button>
      </p>
      <ul v-if="items.length" class="sheet-grid">
        <li v-for="(item, index) in items" :key="item.key" v-seen="item.key" class="tile" :class="{ on: checked.has(item.key) }" :data-key="item.key">
          <button class="tile-pick" role="checkbox" :aria-checked="checked.has(item.key)" :aria-label="label(item)" :title="almost ? 'Cocher pour le flouter quand même' : `Ce flou ${footprint(item)}`" @click="toggle(index, $event)">
            <span v-if="failed.has(item.key)" class="tile-missing"><Icon name="film" :size="20" />Image indisponible</span>
            <img v-else-if="seen.has(item.key)" :src="thumb(item)" loading="lazy" alt="" width="220" height="165" @error="failed.add(item.key)" />
            <span v-else class="tile-missing"></span>
            <span class="tile-check"><Icon name="check" :size="13" /></span>
            <span class="tile-text"><span class="dot" :class="item.track.kind"></span><span class="tile-name">{{ displayName(item.track) }}</span><span class="level" :class="confidence(item.track)" :title="`${confidenceInfo[confidence(item.track)].help} (score ${item.track.best_score.toFixed(2).replace('.', ',')})`">{{ level(item) }}</span></span>
          </button>
          <button class="icon-btn tile-see" :title="`Voir dans la vidéo, à ${formatTime(item.at, 0)}`" :aria-label="`Voir ${displayName(item.track)} dans la vidéo`" @click="see(item)"><Icon name="eye" /></button>
        </li>
      </ul>
      <p v-else class="blank"><Icon name="check" />{{ emptyText }}</p>
      <div class="sheet-foot">
        <span class="grow sheet-count">{{ items.length }} {{ almost ? (items.length > 1 ? "objets non floutés" : "objet non flouté") : (items.length > 1 ? "flous" : "flou") }}<template v-if="count"> · {{ count }} {{ count > 1 ? "cochés" : "coché" }}</template></span>
        <button :disabled="!count" @click="uncheckAll">Tout décocher</button>
        <button class="primary" data-apply :disabled="busy || !count" @click="applyChecked">{{ applyLabel }}</button>
        <button @click="close">Fermer</button>
      </div>
    </div>
  </div>
</template>

<style>
/* === nouveau flux : planche des flous gênants et des objets non floutés === */
.sheet-head { flex-wrap: wrap; row-gap: 8px; }
.sheet-modes { margin-left: 6px; }
.sheet-modes button { gap: 5px; }
.dialog label.sheet-all { flex: none; font-size: 12.5px; color: var(--dim); white-space: nowrap; }
.sheet-offer { background: var(--accent-soft); border: 1px solid var(--accent-line); color: var(--text); animation: fade-in var(--m-panel) var(--ease); }
.sheet-offer > .icon { color: var(--accent); }
/* objets non floutés : cocher = flouter, donc en vert et sans assombrir la vignette */
.sheet.almost .tile.on .tile-pick { border-color: var(--face); background: rgba(74, 222, 128, .1); }
.sheet.almost .tile.on .tile-check { background: var(--face); border-color: var(--face); }
.sheet.almost .tile.on .tile-check .icon, .sheet.almost .tile.on .tile-pick:hover:not(:disabled) .tile-check .icon { color: #04240f; }
.sheet.almost .tile.on img { opacity: 1; }
</style>
