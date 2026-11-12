<script setup lang="ts">
// Planche des flous (mode simple) : tout ce que le logiciel va flouter de lui-même, en vignettes,
// les moins sûrs d'abord. On touche ce qui ne doit PAS être flouté, puis on le retire d'un coup.
// Rien n'est obligatoire : « Continuer » passe à la suite sans rien changer.
import { computed, reactive, ref } from "vue";
import type { Track } from "../api";
import { setTracksDisabled, thumbUrl } from "../extras";
import { busy, mapRange, project } from "../store";
import { clock } from "../wizard";
import Icon from "./Icon.vue";
import WizThumb from "./WizThumb.vue";

const emit = defineEmits<{ done: [] }>();
const PAGE = 60; // au-delà, la planche ne montre d'abord que les flous les moins sûrs

interface Item { key: string; clip: string; track: Track; at: number }

/** Flous automatiques actifs, visibles dans la partie gardée de la vidéo, les plus douteux d'abord. */
const items = computed<Item[]>(() => {
  const out: Item[] = [];
  for (const [clip, tracks] of Object.entries(project.value?.tracks ?? {})) {
    for (const track of tracks) {
      if (track.source !== "auto" || track.disabled || track.below_min_score) continue;
      const part = mapRange({ clip, start: track.start, end: track.end })[0];
      if (part) out.push({ key: `${clip}:${track.id}`, clip, track, at: part.start });
    }
  }
  return out.sort((a, b) => a.track.score - b.track.score || a.track.best_score - b.track.best_score || a.at - b.at);
});
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
function label(item: Item): string {
  return item.track.kind === "plate" ? "Plaque" : item.track.kind === "face" ? "Visage" : item.track.name;
}
function toggle(item: Item): void {
  if (picked.has(item.key)) picked.delete(item.key);
  else picked.add(item.key);
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
  <h1>Voici tout ce qui sera flouté</h1>
  <p v-if="items.length" class="wiz-lead">
    Touchez ce qui ne doit <strong>pas</strong> l'être : un panneau, une affiche, une erreur du logiciel…
    <template v-if="items.length > PAGE">Il y a {{ items.length }} flous : les moins sûrs sont montrés en premier.</template>
  </p>
  <p v-else class="wiz-lead">Le logiciel ne floute rien de lui-même dans cette vidéo.</p>

  <div v-if="items.length" class="sheet-scroll">
    <ul class="sheet-grid" aria-label="Flous automatiques">
      <li v-for="item in shown" :key="item.key">
        <button
          class="sheet-item" :class="{ picked: picked.has(item.key) }" :aria-pressed="picked.has(item.key)" :disabled="working"
          :aria-label="`${label(item)} à ${clock(item.at)}${picked.has(item.key) ? ', choisi pour ne pas être flouté' : ''}`" @click="toggle(item)"
        >
          <WizThumb :src="url(item)" alt="" />
          <span class="sheet-caption"><span>{{ label(item) }}</span><span class="mono">{{ clock(item.at) }}</span></span>
          <span v-if="picked.has(item.key)" class="sheet-mark"><Icon name="close" :size="22" />Ne pas flouter</span>
        </button>
      </li>
    </ul>
    <button v-if="rest > 0" class="sheet-more" @click="limit += PAGE">Afficher {{ rest > PAGE ? `les ${PAGE} suivants` : rest > 1 ? `les ${rest} derniers` : "le dernier" }}<small>{{ shown.length }} affichés sur {{ items.length }}</small></button>
  </div>

  <p class="sheet-status" :class="{ shown: lastCount > 0 }" role="status">
    <template v-if="lastCount"><Icon name="check" />{{ lastCount > 1 ? `${lastCount} flous retirés.` : "1 flou retiré." }}
      <button class="small" :disabled="busy || working" @click="putBack">{{ lastCount > 1 ? "Les remettre" : "Le remettre" }}</button>
    </template>
    <template v-else>&nbsp;</template>
  </p>
  <div class="wiz-actions">
    <template v-if="chosen.length">
      <button class="primary big" data-primary :disabled="busy || working" @click="removeChosen"><Icon name="trash" :size="18" />{{ chosen.length > 1 ? `Retirer les ${chosen.length} flous choisis` : "Retirer le flou choisi" }}</button>
      <button class="big" :disabled="working" @click="picked.clear()">Ne rien retirer</button>
    </template>
    <button v-else class="primary big" data-primary :disabled="working" @click="emit('done')">Continuer<Icon name="arrowRight" :size="18" /></button>
  </div>
</template>

<style>
.sheet-scroll { flex: 1; min-height: 120px; align-self: stretch; overflow-y: auto; padding: 4px 6px 4px 4px; margin: 0 -4px; }
.sheet-grid { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(136px, 1fr)); gap: 10px; }
.sheet-item { position: relative; width: 100%; height: auto; flex-direction: column; align-items: stretch; gap: 5px; padding: 5px 5px 6px; background: var(--surface); border-radius: var(--r-md); font-size: 13px; font-weight: 500; white-space: normal; }
.sheet-caption { display: flex; justify-content: space-between; gap: 6px; padding: 0 3px; color: var(--dim); }
.sheet-caption .mono { color: var(--faint); }
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
