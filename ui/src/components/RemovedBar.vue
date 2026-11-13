<script setup lang="ts">
// Bandeau discret après un retrait fait d'un clic (`state.lastRemoved`) : il ne bloque rien, la
// lecture continue. Il propose d'annuler, de retirer les flous au même endroit et de ne plus
// jamais flouter cet endroit (ces deux derniers seulement s'ils ont un sens). Il s'efface seul
// après quelques secondes, ou dès le retrait suivant.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { neverBlurHere, removeSimilar, restoreRemoved } from "../extras";
import { warnIgnoreOnce } from "../blurs";
import { busy, state, trackById } from "../store";
import Icon from "./Icon.vue";

const DELAY = 8000;
const last = computed(() => state.lastRemoved);
const held = ref(false); // souris ou clavier sur le bandeau : il ne s'efface pas sous la main
let timer = 0;

function arm(): void {
  window.clearTimeout(timer);
  if (last.value && !held.value) timer = window.setTimeout(() => (state.lastRemoved = null), DELAY);
}
watch([last, held], arm, { immediate: true });
// annulé ailleurs (Ctrl+Z, liste des flous) : il n'y a plus rien à proposer
watch(() => state.data.version, () => {
  const l = last.value;
  if (l && l.ids.every((id) => !trackById(l.clip, id)?.disabled)) state.lastRemoved = null;
});
onBeforeUnmount(() => window.clearTimeout(timer));

/** Parmi ces flous d'un clip, ceux qui étaient (ou sont) réellement appliqués : le moteur rapproche
 *  aussi des objets vus au même endroit mais non floutés, qu'on n'annonce pas comme des flous. */
function shown(clip: string, ids: number[]): number {
  return ids.filter((id) => {
    const t = trackById(clip, id);
    return !t || !t.below_min_score || t.forced;
  }).length;
}
const text = computed(() => {
  const l = last.value;
  if (!l) return "";
  const n = Math.max(1, shown(l.clip, l.ids));
  return n > 1 ? `${n} flous retirés` : `Flou retiré · ${l.name}`;
});
const similar = computed(() => (last.value ? shown(last.value.clip, last.value.similar) : 0));
/** « Ne plus jamais flouter cet endroit » n'a de sens que si le même faux flou revient au même endroit. */
const recurring = computed(() => (last.value?.similar.length ?? 0) > 0 || (last.value?.ids.length ?? 0) > 1);

async function never(): Promise<void> {
  await neverBlurHere();
  if (!state.lastRemoved) warnIgnoreOnce();
}
function release(event: FocusEvent): void {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) held.value = false;
}
</script>

<template>
  <div v-if="last" class="removed-bar" @mouseenter="held = true" @mouseleave="held = false" @focusin="held = true" @focusout="release">
    <Icon name="check" />
    <span class="removed-text" role="status">{{ text }}</span>
    <button class="small" :disabled="busy" :title="last.ids.length > 1 ? 'Remettre ces flous' : 'Remettre ce flou'" data-removed="undo" @click="restoreRemoved()"><Icon name="undo" :size="13" />Annuler</button>
    <button
      v-if="similar" class="small" :disabled="busy" data-removed="similar"
      title="D'autres flous du même genre sont au même endroit de l'image, à d'autres moments : c'est sans doute la même erreur qui revient"
      @click="removeSimilar()"
    >{{ similar > 1 ? `Retirer aussi les ${similar} flous au même endroit` : "Retirer aussi l'autre flou au même endroit" }}</button>
    <button
      v-if="recurring" class="small" :disabled="busy" data-removed="never"
      title="Plus aucun flou automatique à cet endroit de l'image, dans tout ce clip, maintenant et après une nouvelle analyse. Attention : un visage ou une plaque qui se tiendrait là ne serait pas flouté."
      @click="never"
    >Ne plus jamais flouter cet endroit</button>
    <span class="spacer"></span>
    <button class="icon-btn bare" aria-label="Fermer ce message" title="Fermer" @click="state.lastRemoved = null"><Icon name="close" :size="14" /></button>
  </div>
</template>

<style>
/* === nouveau flux : bandeau après un retrait === */
.removed-bar { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; animation: fade-in var(--m-panel) var(--ease); }
.removed-bar > .icon { color: var(--face); }
.removed-text { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.removed-bar button.small { flex: 0 1 auto; min-width: 0; overflow: hidden; }
.removed-bar .spacer { min-width: 0; }
</style>
