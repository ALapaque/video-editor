<script lang="ts">
// Planche des flous : tous les flous automatiques en vignettes, les plus douteux d'abord, pour
// retirer les faux flous par dizaines. Un clic coche ou décoche ; un bouton retire tout ce qui
// est coché en une seule action (qui s'annule avec Ctrl+Z).
import { reactive } from "vue";

/** Flous cochés (« clip:numéro ») : gardés quand la planche se ferme pour aller voir un flou dans la vidéo. */
const checked = reactive(new Set<string>());
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { setTracksDisabled, thumbUrl } from "../extras";
import { closeSheet, review, sheetItems, type SheetItem } from "../review";
import { busy, formatTime, notify, player, seekTime, state } from "../store";
import Icon from "./Icon.vue";

const box = ref<HTMLDivElement>();
let opener: Element | null = null;

/** Flous montrés : tous, ou seulement ceux du passage depuis lequel la planche a été ouverte. */
const items = computed(() => {
  const filter = review.sheetFilter;
  if (!filter) return sheetItems.value;
  const ids = new Set(filter.ids);
  return sheetItems.value.filter((i) => i.clip === filter.clip && ids.has(i.track.id));
});
function showAll(): void {
  review.sheetFilter = null;
  last = -1;
}
const selected = computed(() => items.value.filter((i) => checked.has(i.key)));
const count = computed(() => selected.value.length);
const removeLabel = computed(() =>
  count.value === 0 ? "Retirer les flous cochés" : count.value === 1 ? "Retirer le flou coché" : `Retirer les ${count.value} flous cochés`,
);

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

function score(item: SheetItem): string {
  return item.track.best_score.toFixed(2).replace(".", ",");
}
function label(item: SheetItem): string {
  return `${item.track.name} n° ${item.track.id}, score ${score(item)}, à ${formatTime(item.at, 0)}`;
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

async function removeChecked(): Promise<void> {
  const byClip = new Map<string, number[]>();
  for (const item of selected.value) byClip.set(item.clip, [...(byClip.get(item.clip) ?? []), item.track.id]);
  const before = count.value;
  if (!before) return;
  for (const [clip, ids] of byClip) await setTracksDisabled(clip, ids, true);
  // ce qui est encore dans la planche n'a pas pu être retiré (le moteur a dit pourquoi) : ça reste coché
  const still = new Set(sheetItems.value.map((i) => i.key));
  for (const key of [...checked]) if (!still.has(key)) checked.delete(key);
  last = -1;
  const n = before - count.value;
  if (n) notify(`${n > 1 ? `${n} flous retirés` : "1 flou retiré"}. Pour ${n > 1 ? "les" : "le"} remettre : « Annuler » (Ctrl+Z).`);
}

/** Aller voir un flou dans la vidéo : la planche se ferme, les cases cochées sont gardées. */
function see(item: SheetItem): void {
  review.sheet = false; // sans relancer la boucle de vérification : on regarde ce flou, à l'arrêt
  review.sheetFilter = null;
  player.pause();
  seekTime(item.at);
  state.preview = "boxes"; // le flou se juge sur l'image nette, entourée
  state.selectedTrack = { clip: item.clip, id: item.track.id };
  notify(`Flou n° ${item.track.id} : il est entouré d'un trait épais dans l'image. Rouvrez la planche pour continuer${checked.size ? ", vos cases cochées sont gardées" : ""}.`);
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
onMounted(() => {
  opener = document.activeElement;
  // les cases de flous qui n'existent plus (retirés ailleurs, nouvelle analyse) sont oubliées
  const known = new Set(sheetItems.value.map((i) => i.key));
  for (const key of [...checked]) if (!known.has(key)) checked.delete(key);
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
    <div ref="box" class="dialog sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title" tabindex="-1">
      <div class="sheet-head">
        <h3 id="sheet-title"><Icon name="grid" :size="18" />Planche des flous</h3>
        <button class="icon-btn bare" aria-label="Fermer la planche" title="Fermer (Échap)" @click="close"><Icon name="close" /></button>
      </div>
      <p>{{ review.sheetFilter ? "Les flous posés par le logiciel dans le passage que vous vérifiez" : "Tous les flous posés par le logiciel" }}, les plus douteux d'abord. Cliquez les faux flous (reflet, feuillage, panneau…) pour les cocher, puis retirez-les d'un coup. Maj + clic coche toute une série.</p>
      <p v-if="review.sheetFilter" class="sheet-scope">
        <span class="grow">Seuls les flous de ce passage sont affichés.</span>
        <button class="small" @click="showAll">Voir tous les flous du montage ({{ sheetItems.length }})</button>
      </p>
      <ul v-if="items.length" class="sheet-grid">
        <li v-for="(item, index) in items" :key="item.key" v-seen="item.key" class="tile" :class="{ on: checked.has(item.key) }">
          <button class="tile-pick" role="checkbox" :aria-checked="checked.has(item.key)" :aria-label="label(item)" @click="toggle(index, $event)">
            <span v-if="failed.has(item.key)" class="tile-missing"><Icon name="film" :size="20" />Image indisponible</span>
            <img v-else-if="seen.has(item.key)" :src="thumb(item)" loading="lazy" alt="" width="220" height="165" @error="failed.add(item.key)" />
            <span v-else class="tile-missing"></span>
            <span class="tile-check"><Icon name="check" :size="13" /></span>
            <span class="tile-text"><span class="dot" :class="item.track.kind"></span><span class="tile-name">{{ item.track.name }} n° {{ item.track.id }}</span><span class="mono">{{ score(item) }}</span></span>
          </button>
          <button class="icon-btn tile-see" :title="`Voir ce flou dans la vidéo, à ${formatTime(item.at, 0)}`" :aria-label="`Voir le flou n° ${item.track.id} dans la vidéo`" @click="see(item)"><Icon name="eye" /></button>
        </li>
      </ul>
      <p v-else class="blank"><Icon name="check" />{{ review.sheetFilter ? "Plus aucun flou automatique dans ce passage." : "Aucun flou automatique dans ce montage." }}</p>
      <div class="sheet-foot">
        <span class="grow sheet-count">{{ items.length }} {{ items.length > 1 ? "flous" : "flou" }}<template v-if="count"> · {{ count }} {{ count > 1 ? "cochés" : "coché" }}</template></span>
        <button :disabled="!count" @click="checked.clear()">Tout décocher</button>
        <button class="primary" :disabled="busy || !count" @click="removeChecked">{{ removeLabel }}</button>
        <button @click="close">Fermer</button>
      </div>
    </div>
  </div>
</template>
