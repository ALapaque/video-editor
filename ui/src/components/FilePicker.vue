<script setup lang="ts">
// Sélecteur de fichiers servi par le moteur : un navigateur ne donne pas le chemin réel d'un
// fichier, or le moteur en a besoin pour lire la vidéo sans la copier.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { call, type Browse } from "../api";
import Icon from "./Icon.vue";

// `simple` : présentation du mode simple, vidéos seulement, sans saisie de chemin.
// `multiple` : on coche plusieurs vidéos (dans un ou plusieurs dossiers), puis on les ajoute
// d'un coup ; `taken` : chemins déjà choisis ailleurs, montrés cochés et non modifiables.
const props = defineProps<{ title: string; start?: string; simple?: boolean; multiple?: boolean; taken?: string[] }>();
const emit = defineEmits<{ pick: [path: string]; pickMany: [paths: string[]]; close: [] }>();
const listing = ref<Browse | null>(null);
const error = ref("");
const typed = ref("");
const chosen = ref<string[]>([]); // `multiple` : chemins cochés, dans l'ordre des clics

async function go(dir?: string): Promise<void> {
  try {
    listing.value = await call<Browse>("GET", `/api/browse${dir ? `?dir=${encodeURIComponent(dir)}` : ""}`);
    typed.value = listing.value.dir;
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
}
/** Mode simple : seulement les vidéos (un fichier projet s'ouvre en choisissant sa vidéo). */
const shownFiles = computed(() => (listing.value?.files ?? []).filter((f) => !props.simple || !f.name.endsWith(".json")));
function join(name: string): string {
  const l = listing.value!;
  return l.dir.endsWith(l.sep) ? l.dir + name : l.dir + l.sep + name;
}
function size(bytes: number): string {
  return bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} Go` : `${Math.max(1, Math.round(bytes / 1e6))} Mo`;
}
function submitTyped(): void {
  const value = typed.value.trim();
  if (/\.(mp4|mov|mkv|m4v|avi|mts|webm|json)$/i.test(value)) {
    if (props.multiple) emit("pickMany", [value]);
    else emit("pick", value);
  } else void go(value);
}

// --- Plusieurs vidéos à la fois ---
function isTaken(name: string): boolean {
  return props.taken?.includes(join(name)) ?? false;
}
function toggle(name: string): void {
  const path = join(name);
  if (isTaken(name)) return;
  chosen.value = chosen.value.includes(path) ? chosen.value.filter((p) => p !== path) : [...chosen.value, path];
}
/** Vidéos de ce dossier qui peuvent encore être cochées. */
const free = computed(() => shownFiles.value.map((f) => join(f.name)).filter((p) => !props.taken?.includes(p)));
const allHere = computed(() => free.value.length > 0 && free.value.every((p) => chosen.value.includes(p)));
function toggleAll(): void {
  chosen.value = allHere.value ? chosen.value.filter((p) => !free.value.includes(p)) : [...chosen.value, ...free.value.filter((p) => !chosen.value.includes(p))];
}
function addChosen(): void {
  if (chosen.value.length) emit("pickMany", chosen.value);
}

function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}
onMounted(() => {
  window.addEventListener("keydown", onKey);
  void go(props.start);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div class="dialog picker" role="dialog" aria-modal="true" aria-labelledby="picker-title">
      <h3 id="picker-title"><Icon name="folder" :size="18" />{{ title }}</h3>
      <p v-if="simple && listing" class="picker-where"><span class="dim">Dossier :</span> <span class="mono">{{ listing.dir }}</span></p>
      <form v-else-if="!simple" class="row" @submit.prevent="submitTyped">
        <input v-model="typed" type="text" spellcheck="false" placeholder="Chemin d'un dossier ou d'une vidéo" aria-label="Chemin d'un dossier ou d'une vidéo" />
        <button type="submit">Aller</button>
      </form>
      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="listing" class="row shortcuts">
        <button :disabled="!listing.parent" @click="go(listing.parent!)"><Icon name="arrowUp" />Dossier parent</button>
        <button @click="go(listing.home)"><Icon name="home" />Dossier personnel</button>
        <button v-for="r in listing.roots" :key="r" @click="go(r)"><Icon name="drive" />{{ r }}</button>
        <template v-if="multiple && free.length > 1">
          <span class="spacer"></span>
          <button data-all :aria-pressed="allHere" @click="toggleAll"><Icon name="check" />{{ allHere ? "Tout décocher" : "Tout cocher dans ce dossier" }}</button>
        </template>
      </div>
      <ul v-if="listing" :key="listing.dir" class="files" :class="{ multiple }">
        <li v-for="f in listing.folders" :key="'d' + f" class="folder" @click="go(join(f))"><Icon name="folder" /><span class="fname">{{ f }}</span><Icon class="go" name="chevronRight" :size="14" /></li>
        <template v-if="multiple">
          <li v-for="f in shownFiles" :key="'f' + f.name" class="file pickable" :class="{ on: chosen.includes(join(f.name)) || isTaken(f.name), taken: isTaken(f.name) }">
            <label>
              <input type="checkbox" :checked="chosen.includes(join(f.name)) || isTaken(f.name)" :disabled="isTaken(f.name)" @change="toggle(f.name)" />
              <Icon name="film" /><span class="fname">{{ f.name }}</span>
              <span v-if="isTaken(f.name)" class="dim">déjà dans la liste</span>
              <span v-else class="dim mono">{{ size(f.size) }}</span>
            </label>
          </li>
        </template>
        <template v-else>
          <li v-for="f in shownFiles" :key="'f' + f.name" class="file" @click="emit('pick', join(f.name))">
            <Icon name="film" /><span class="fname">{{ f.name }}</span> <span class="dim mono">{{ size(f.size) }}</span>
          </li>
        </template>
        <li v-if="!listing.folders.length && !shownFiles.length" class="dim none">Aucune vidéo ni dossier ici.</li>
      </ul>
      <div v-else-if="!error" class="files loading"><span class="spinner"></span>Lecture du dossier…</div>
      <div class="row end">
        <template v-if="multiple">
          <span class="picker-count dim" role="status">{{ chosen.length ? (chosen.length > 1 ? `${chosen.length} vidéos cochées` : "1 vidéo cochée") : "Cochez les vidéos à ajouter." }}</span>
          <span class="spacer"></span>
        </template>
        <button @click="emit('close')">Annuler</button>
        <button v-if="multiple" class="primary" data-add :disabled="!chosen.length" @click="addChosen"><Icon name="plus" />{{ chosen.length > 1 ? `Ajouter les ${chosen.length} vidéos` : "Ajouter à la liste" }}</button>
      </div>
    </div>
  </div>
</template>

<style>
/* plusieurs vidéos à la fois : chaque ligne est une case à cocher */
.files.multiple li.pickable { padding: 0; }
.files li.pickable label { flex: 1; min-width: 0; flex-direction: row; align-items: center; gap: 9px; padding: 7px 9px; color: var(--text); font-size: inherit; cursor: pointer; }
.files li.pickable.on { background: var(--accent-soft); }
.files li.pickable.taken, .files li.pickable.taken label { cursor: default; }
.files li.pickable.taken:hover { background: var(--accent-soft); }
.files li.pickable.taken .fname { color: var(--dim); }
.picker .row.end { align-items: center; }
.picker-count { font-size: 12.5px; }
</style>
