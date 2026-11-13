<script setup lang="ts">
// « Libérer de la place » : les fichiers d'aperçu que le logiciel a fabriqués, projet par projet,
// avec leur taille et un bouton pour les supprimer. Composant autonome (aucune dépendance à
// l'assistant) : il s'ouvre depuis n'importe quel écran, dans les deux modes.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { Storage } from "../api";
import { deletePreviews, formatSize, storage } from "../extras";
import Icon from "./Icon.vue";

const emit = defineEmits<{ close: [] }>();
const data = ref<Storage | null>(null);
const loading = ref(true);
const failed = ref(false);
const deleting = ref(""); // dossier en cours de suppression ; "*" : tous
const freed = ref(0); // place libérée depuis l'ouverture du dialogue
const box = ref<HTMLDivElement>();

const previews = computed(() => data.value?.previews ?? []);
const total = computed(() => previews.value.reduce((sum, p) => sum + p.size, 0));
const removable = computed(() => previews.value.filter((p) => !p.current));

async function load(): Promise<void> {
  loading.value = true;
  failed.value = false;
  const result = await storage();
  loading.value = false;
  if (result) data.value = result;
  else failed.value = true;
}
async function remove(path: string): Promise<void> {
  if (deleting.value) return;
  deleting.value = path;
  const result = await deletePreviews(path);
  deleting.value = "";
  if (!result) return; // le message d'erreur est déjà affiché
  freed.value += result.freed ?? 0;
  data.value = result;
}
async function removeAll(): Promise<void> {
  if (deleting.value) return;
  deleting.value = "*";
  for (const p of [...removable.value]) {
    const result = await deletePreviews(p.path);
    if (!result) break;
    freed.value += result.freed ?? 0;
    data.value = result;
  }
  deleting.value = "";
}
/** Dossier où se trouve le projet : deux projets peuvent porter le même nom. */
function folderOf(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? `dans ${parts[parts.length - 2]}` : "";
}
function onKey(event: KeyboardEvent): void {
  if (event.key !== "Escape") return;
  event.stopPropagation();
  emit("close");
}
onMounted(() => {
  window.addEventListener("keydown", onKey);
  box.value?.focus();
  void load();
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div ref="box" class="dialog storage" role="dialog" aria-modal="true" aria-labelledby="storage-title" tabindex="-1">
      <h3 id="storage-title"><Icon name="drive" :size="18" />Libérer de la place</h3>
      <p>Ces fichiers servent seulement à l'aperçu ; ils se refabriquent à la prochaine ouverture du projet. Vos vidéos ne sont jamais touchées.</p>

      <div v-if="loading" class="storage-state"><span class="spinner"></span>Calcul de la place occupée…</div>
      <div v-else-if="failed" class="storage-state">
        <span>La place occupée n'a pas pu être calculée.</span>
        <button @click="load"><Icon name="refresh" />Réessayer</button>
      </div>
      <template v-else>
        <ul v-if="previews.length" class="storage-list">
          <li v-for="p in previews" :key="p.path" :class="{ gone: deleting === p.path || (deleting === '*' && !p.current) }">
            <Icon name="film" />
            <span class="storage-name" :title="p.path">{{ p.name }}</span>
            <span class="storage-where" :title="p.path">{{ folderOf(p.path) }}</span>
            <span v-if="p.current" class="tag">ouvert en ce moment</span>
            <span v-else-if="p.orphan" class="tag">projet supprimé</span>
            <span class="spacer"></span>
            <span class="mono storage-size">{{ formatSize(p.size) }}</span>
            <button
              class="small danger" :disabled="p.current || !!deleting" :aria-label="`Supprimer les fichiers d'aperçu de ${p.name}`"
              :title="p.current ? 'Ce projet est ouvert : ses fichiers d\'aperçu servent en ce moment.' : 'Supprimer ces fichiers d\'aperçu'" @click="remove(p.path)"
            ><Icon name="trash" :size="14" />Supprimer</button>
          </li>
        </ul>
        <p v-else class="storage-state">Aucun fichier d'aperçu pour l'instant : rien à supprimer.</p>

        <p v-if="freed" class="storage-freed" role="status"><Icon name="check" />{{ formatSize(freed) }} libérés.</p>
        <dl class="storage-totals">
          <div><dt>Total des fichiers d'aperçu</dt><dd class="mono">{{ previews.length ? formatSize(total) : "0 Mo" }}</dd></div>
          <div><dt>Modèles d'analyse <small>nécessaires au logiciel, ils ne se suppriment pas ici</small></dt><dd class="mono">{{ data && data.models ? formatSize(data.models) : "pas encore téléchargés" }}</dd></div>
        </dl>
      </template>

      <div class="row end storage-actions">
        <button v-if="removable.length > 1" class="danger" :disabled="!!deleting" @click="removeAll"><Icon name="trash" />Tout supprimer</button>
        <span class="spacer"></span>
        <button data-close @click="emit('close')">Fermer</button>
      </div>
    </div>
  </div>
</template>

<style>
.storage { width: min(620px, 92vw); font-size: 14px; }
.storage:focus { outline: none; }
.storage > p { line-height: 1.5; }
.storage-state { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 96px; padding: 12px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; color: var(--dim); text-align: center; }
.storage-list { margin: 0; padding: 4px; list-style: none; max-height: 38vh; overflow-y: auto; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; }
.storage-list li { display: flex; align-items: center; gap: 9px; min-height: 40px; padding: 5px 6px 5px 9px; border-radius: 5px; transition: opacity var(--m-out) var(--ease); }
.storage-list li + li { border-top: 1px solid var(--line); border-radius: 0; }
.storage-list li.gone { opacity: .45; }
.storage-list li > .icon { color: var(--dim); }
.storage-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); font-weight: 500; }
.storage-where { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--faint); font-size: 12.5px; }
.storage-list .tag { flex: none; color: var(--dim); }
.storage-size { flex: none; color: var(--dim); }
.storage-list button.small { flex: none; height: 28px; padding: 0 9px; font-size: 13px; }
.storage-freed { display: flex; align-items: center; gap: 8px; color: var(--face) !important; }
.storage-totals { margin: 0; display: flex; flex-direction: column; gap: 2px; }
.storage-totals > div { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 6px 2px; border-top: 1px solid var(--line); }
.storage-totals dt { color: var(--dim); }
.storage-totals dt small { display: block; color: var(--faint); font-size: 12px; }
.storage-totals dd { margin: 0; flex: none; color: var(--text); }
.storage-actions { display: flex; align-items: center; gap: 8px; }
.storage-actions button { height: 34px; padding: 0 14px; }
</style>
