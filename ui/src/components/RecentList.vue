<script setup lang="ts">
// Vidéos (projets) ouvertes récemment : un clic rouvre le projet là où il en était.
// Composant autonome, utilisable dans les deux modes : il ne fait que lister et prévenir du choix.
import { computed, onMounted, ref } from "vue";
import type { Recent } from "../api";
import { recentProjects } from "../extras";
import { dayText } from "../wizard";
import Icon from "./Icon.vue";

const props = withDefaults(defineProps<{
  /** Projet à ne pas proposer (celui qui est déjà ouvert). */
  exclude?: string;
  /** Nombre de lignes affichées avant « Afficher les autres ». */
  limit?: number;
  disabled?: boolean;
}>(), { exclude: "", limit: 4, disabled: false });
const emit = defineEmits<{ open: [path: string] }>();

const all = ref<Recent[]>([]);
const expanded = ref(false);
const list = computed(() => all.value.filter((r) => r.path !== props.exclude));
const shown = computed(() => (expanded.value ? list.value : list.value.slice(0, props.limit)));
const hidden = computed(() => list.value.length - shown.value.length);

async function reload(): Promise<void> {
  all.value = await recentProjects(); // liste vide si le moteur ne répond pas : la section ne s'affiche pas
}
/** Les deux derniers dossiers du chemin : assez pour s'y retrouver, sans noyer la ligne. */
function where(folder: string): string {
  const parts = folder.split(/[\\/]/).filter(Boolean);
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : folder;
}
onMounted(reload);
defineExpose({ reload });
</script>

<template>
  <section v-if="list.length" class="recents" aria-labelledby="recents-title">
    <h2 id="recents-title">Reprendre une vidéo récente</h2>
    <ul>
      <li v-for="r in shown" :key="r.path">
        <button class="recent" :disabled="disabled" :title="`Rouvrir ${r.path}`" @click="emit('open', r.path)">
          <Icon name="film" :size="18" />
          <span class="recent-name">{{ r.name }}</span>
          <span class="recent-where">{{ where(r.folder) }}</span>
          <span class="recent-when">{{ dayText(r.modified) }}</span>
          <Icon class="recent-go" name="chevronRight" :size="14" />
        </button>
      </li>
    </ul>
    <button v-if="hidden > 0" class="quiet recents-more" @click="expanded = true">{{ hidden > 1 ? `Afficher les ${hidden} autres` : "Afficher l'autre" }}</button>
  </section>
</template>

<style>
.recents { align-self: stretch; display: flex; flex-direction: column; gap: 8px; margin-top: 14px; text-align: left; }
.recents h2 { margin: 0; color: var(--dim); font-size: 14px; font-weight: 600; letter-spacing: 0; }
.recents ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.recent { width: 100%; height: 46px; justify-content: flex-start; gap: 11px; padding: 0 12px 0 14px; background: var(--surface); border-radius: var(--r-md); font-size: 15px; font-weight: 500; }
.recent-name { flex: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.recent-where { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--faint); font-size: 13px; font-weight: 400; text-align: left; }
.recent-when { flex: none; color: var(--dim); font-size: 13px; font-weight: 400; }
.recent .recent-go { color: var(--faint); }
.recents-more { align-self: flex-start; height: 32px; font-size: 14px; }
.recents-more .icon { color: var(--dim); }
@media (max-width: 620px) { .recent-where { display: none; } .recent-name { flex: 1; max-width: none; text-align: left; } }
</style>
