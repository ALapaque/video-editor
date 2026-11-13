<script setup lang="ts">
// Planche facultative du mode simple : « Les flous qui se voient le plus », en vignettes, les plus
// gênants d'abord (gros ou longs). On touche ceux qui sont en trop, puis on les retire d'un coup.
// Rien n'est à valider ici : tout ce qui reste est flouté. « Voir tous les flous » montre aussi
// les petits flous brefs. La planche n'est jamais sur le chemin principal de l'assistant.
import { computed, reactive, ref } from "vue";
import type { Track } from "../api";
import { annoyingBlurs, appliedBlurs, blurName, setTracksDisabled, thumbUrl } from "../extras";
import { busy, mapRange, project } from "../store";
import { clock } from "../wizard";
import Icon from "./Icon.vue";
import WizThumb from "./WizThumb.vue";

const emit = defineEmits<{ done: [] }>();
const PAGE = 60; // au-delà, la planche ne montre d'abord que les flous les plus visibles

interface Item { key: string; clip: string; track: Track; at: number }

/** Flous choisis par `pick` dans chaque vidéo, visibles dans la partie gardée, les plus gênants d'abord. */
function collect(pick: (tracks: Track[]) => Track[]): Item[] {
  const out: Item[] = [];
  for (const [clip, tracks] of Object.entries(project.value?.tracks ?? {})) {
    for (const track of pick(tracks)) {
      const part = mapRange({ clip, start: track.start, end: track.end })[0];
      if (part) out.push({ key: `${clip}:${track.id}`, clip, track, at: part.start });
    }
  }
  return out.sort((a, b) => b.track.nuisance - a.track.nuisance || a.at - b.at);
}
const annoying = computed(() => collect(annoyingBlurs));
const all = computed(() => collect(appliedBlurs));
const everything = ref(false); // « Voir tous les flous »
const items = computed(() => (everything.value ? all.value : annoying.value));
const limit = ref(PAGE);
const shown = computed(() => items.value.slice(0, limit.value));
const rest = computed(() => items.value.length - shown.value.length);

const picked = reactive(new Set<string>());
const chosen = computed(() => items.value.filter((i) => picked.has(i.key)));
const last = ref<{ clip: string; ids: number[] }[]>([]); // dernier retrait, pour pouvoir le défaire ici même
const lastCount = computed(() => last.value.reduce((n, g) => n + g.ids.length, 0));
const working = ref(false);

// L'adresse d'une vignette change avec chaque modification du projet : on garde la première,
// sinon toute la planche se rechargerait après chaque retrait.
const urls = new Map<string, string>();
function url(item: Item): string {
  let u = urls.get(item.key);
  if (!u) urls.set(item.key, (u = thumbUrl(item.clip, item.track.id, 240)));
  return u;
}
/** « un instant », « 2,5 s », « 12 s » : combien de temps le flou reste à l'écran. */
function lasts(track: Track): string {
  const t = track.end - track.start;
  if (t < 1) return "un instant";
  return `${t < 9.95 ? t.toFixed(1).replace(".", ",") : Math.round(t)} s`;
}
function toggle(item: Item): void {
  if (picked.has(item.key)) picked.delete(item.key);
  else picked.add(item.key);
}
function showAll(on: boolean): void {
  everything.value = on;
  limit.value = PAGE;
  const visible = new Set(items.value.map((i) => i.key));
  for (const key of [...picked]) if (!visible.has(key)) picked.delete(key); // un flou qu'on ne voit plus n'est plus choisi
}
function grouped(list: Item[]): { clip: string; ids: number[] }[] {
  const groups = new Map<string, number[]>();
  for (const i of list) groups.set(i.clip, [...(groups.get(i.clip) ?? []), i.track.id]);
  return [...groups].map(([clip, ids]) => ({ clip, ids }));
}
async function removeChosen(): Promise<void> {
  if (working.value || !chosen.value.length) return;
  working.value = true;
  const groups = grouped(chosen.value);
  for (const g of groups) await setTracksDisabled(g.clip, g.ids, true);
  // ce qui a vraiment été retiré (un appel refusé laisse ses flous cochés, avec le message d'erreur)
  const gone = groups.map((g) => ({ clip: g.clip, ids: g.ids.filter((id) => project.value?.tracks[g.clip]?.find((t) => t.id === id)?.disabled) })).filter((g) => g.ids.length);
  for (const g of gone) for (const id of g.ids) picked.delete(`${g.clip}:${id}`);
  if (gone.length) last.value = gone;
  working.value = false;
}
async function putBack(): Promise<void> {
  if (working.value) return;
  working.value = true;
  for (const g of last.value) await setTracksDisabled(g.clip, g.ids, false);
  last.value = [];
  working.value = false;
}
</script>

<template>
  <h1>{{ everything ? "Tous les flous" : "Les flous qui se voient le plus" }}</h1>
  <p v-if="items.length" class="wiz-lead">
    Touchez ceux qui sont en trop : un panneau, une affiche, une erreur du logiciel… Les autres restent floutés.
    <template v-if="items.length > PAGE">Il y en a {{ items.length }} : les plus visibles sont montrés en premier.</template>
  </p>
  <p v-else-if="all.length" class="wiz-lead" data-none>Aucun flou ne prend beaucoup de place dans cette vidéo. Il n'y a rien à retirer d'un coup.</p>
  <p v-else class="wiz-lead" data-none>Le logiciel ne floute rien de lui-même dans cette vidéo.</p>

  <div v-if="items.length" class="sheet-scroll">
    <ul class="sheet-grid" :aria-label="everything ? 'Tous les flous' : 'Les flous qui se voient le plus'">
      <li v-for="item in shown" :key="item.key">
        <button
          class="sheet-item" :class="{ picked: picked.has(item.key) }" :aria-pressed="picked.has(item.key)" :disabled="working"
          :aria-label="`${blurName(item.track)} à ${clock(item.at)}, pendant ${lasts(item.track)}${picked.has(item.key) ? ', choisi pour être retiré' : ''}`" @click="toggle(item)"
        >
          <WizThumb :src="url(item)" alt="" />
          <span class="sheet-caption"><span>{{ blurName(item.track) }}</span><span class="mono">{{ clock(item.at) }}</span></span>
          <span class="sheet-level">pendant {{ lasts(item.track) }}</span>
          <span v-if="picked.has(item.key)" class="sheet-mark"><Icon name="close" :size="22" />À retirer</span>
        </button>
      </li>
    </ul>
    <button v-if="rest > 0" class="sheet-more" @click="limit += PAGE">Afficher {{ rest > PAGE ? `les ${PAGE} suivants` : rest > 1 ? `les ${rest} derniers` : "le dernier" }}<small>{{ shown.length }} affichés sur {{ items.length }}</small></button>
  </div>

  <p class="sheet-status" :class="{ shown: lastCount > 0 }" role="status">
    <template v-if="lastCount"><Icon name="check" />{{ lastCount > 1 ? `${lastCount} flous retirés.` : "1 flou retiré." }}
      <button class="small" data-put-back :disabled="busy || working" @click="putBack">{{ lastCount > 1 ? "Les remettre" : "Le remettre" }}</button>
    </template>
    <template v-else>&nbsp;</template>
  </p>
  <div class="wiz-actions">
    <template v-if="chosen.length">
      <button class="primary big" data-primary :disabled="busy || working" @click="removeChosen"><Icon name="trash" :size="18" />{{ chosen.length > 1 ? `Retirer les ${chosen.length} flous choisis` : "Retirer le flou choisi" }}</button>
      <button class="big" :disabled="working" @click="picked.clear()">Ne rien retirer</button>
    </template>
    <button v-else class="primary big" data-primary :disabled="working" @click="emit('done')">J'ai terminé<Icon name="arrowRight" :size="18" /></button>
    <button v-if="!everything && all.length > annoying.length" class="big quiet" data-all @click="showAll(true)"><Icon name="grid" :size="18" />Voir tous les flous</button>
    <button v-else-if="everything" class="big quiet" data-all @click="showAll(false)">Voir seulement ceux qui se voient le plus</button>
  </div>
</template>

<style>
.sheet-scroll { flex: 1; min-height: 120px; align-self: stretch; overflow-y: auto; padding: 4px 6px 4px 4px; margin: 0 -4px; }
.sheet-grid { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(136px, 1fr)); gap: 10px; }
.sheet-item { position: relative; width: 100%; height: auto; flex-direction: column; align-items: stretch; gap: 5px; padding: 5px 5px 6px; background: var(--surface); border-radius: var(--r-md); font-size: 13px; font-weight: 500; white-space: normal; }
.sheet-caption { display: flex; justify-content: space-between; gap: 6px; padding: 0 3px; color: var(--dim); }
.sheet-caption .mono { color: var(--faint); }
.sheet-level { align-self: flex-start; margin: -2px 3px 0; color: var(--faint); font-size: 12px; font-weight: 500; }
.sheet-item.picked { background: var(--danger-soft); border-color: var(--danger); }
.sheet-item.picked .wiz-thumb img { filter: grayscale(1) brightness(.45); }
.sheet-item.picked .sheet-caption { color: var(--text); }
.sheet-mark { position: absolute; top: 5px; left: 5px; right: 5px; aspect-ratio: 4 / 3; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: #fff; font-size: 13px; font-weight: 650; pointer-events: none; }
.sheet-mark .icon { color: var(--danger); }
.sheet-more { width: 100%; height: auto; flex-direction: column; gap: 2px; margin-top: 12px; padding: 11px 14px; font-size: 15px; border-radius: var(--r-md); }
.sheet-more small { color: var(--dim); font-size: 12.5px; font-weight: 400; }
.sheet-status { display: flex; align-items: center; gap: 9px; min-height: 32px; color: var(--face); font-size: 15px; opacity: 0; transition: opacity var(--m-in) var(--ease); }
.sheet-status.shown { opacity: 1; }
.sheet-status button.small { height: 30px; padding: 0 11px; font-size: 14px; }
</style>
