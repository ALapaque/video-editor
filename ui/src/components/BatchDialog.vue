<script setup lang="ts">
// « Analyser plusieurs vidéos » : la file d'attente. On choisit des vidéos, on lance, et le
// logiciel les analyse l'une après l'autre (chacune a son projet, créé à côté d'elle). Trois
// vues : la liste à préparer, la file en cours, le résultat. Le résultat reste affiché tant que
// l'utilisateur ne demande pas une nouvelle file : rien ne s'efface tout seul pendant la nuit.
// Composant commun aux deux modes ; `simple` ne change que le sélecteur de fichiers.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { BatchResult } from "../api";
import {
  type BatchPreset, addBatchPaths, batch, batchItemText, batchJob, batchPercent, batchRunning, fileName, launchBatch, newBatch, removeBatchPath,
} from "../batch";
import { choosablePresets, presetInfo } from "../extras";
import { busy, send, state } from "../store";
import { dayText } from "../wizard";
import FilePicker from "./FilePicker.vue";
import Icon from "./Icon.vue";

defineProps<{ simple?: boolean }>();
/** `open` : ouvrir le projet d'une vidéo analysée (le dialogue se ferme, le résultat est gardé). */
const emit = defineEmits<{ open: [path: string] }>();

const box = ref<HTMLDivElement>();
const picking = ref(false);
const confirming = ref(false);
const launching = ref(false);
const error = ref("");
let opener: Element | null = null;

const view = computed<"running" | "result" | "form">(() => (batchRunning.value ? "running" : batch.last ? "result" : "form"));

function nb(text: string): string {
  return text.replace(/ ([:;?!»])/g, " $1").replace(/« /g, "« ");
}
function plural(n: number, one: string, many: string): string {
  return `${n} ${n > 1 ? many : one}`;
}
/** Dossier d'un fichier : les deux derniers niveaux suffisent pour s'y retrouver. */
function folderOf(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean).slice(0, -1);
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : parts.join("/");
}
function dirOf(path: string): string {
  return path.replace(/[\\/][^\\/]*$/, "");
}
/** « 45 s », « 3 min 05 s », « 1 h 12 min ». */
function took(seconds: number): string {
  const s = Math.max(1, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

// --- La liste à préparer ---
const profile = computed(() => state.data.profile ?? null);
const useProfile = computed(() => batch.profile && !!profile.value);
/** Avec les réglages habituels, le type de vidéo est celui qu'ils contiennent. */
const shownPreset = computed(() => (useProfile.value && profile.value ? profile.value.preset : batch.preset));
const profileText = computed(() => {
  const saved = profile.value;
  if (!saved) return "";
  const words: string[] = saved.kinds.map((k) => (k === "face" ? "les visages" : "les plaques"));
  words.push(...saved.objects.map((o) => `« ${o.charAt(0).toUpperCase()}${o.slice(1)} »`));
  return words.length > 1 ? `${words.slice(0, -1).join(", ")} et ${words[words.length - 1]}` : (words[0] ?? "");
});
/** Dossier où rouvrir le sélecteur : celui de la dernière vidéo ajoutée. */
const startDir = computed(() => (batch.paths.length ? dirOf(batch.paths[batch.paths.length - 1]) : undefined));
function add(paths: string[]): void {
  picking.value = false;
  addBatchPaths(paths);
  batch.note = "";
  error.value = "";
  focusMain();
}
async function launch(): Promise<void> {
  if (!batch.paths.length || busy.value || launching.value) return;
  launching.value = true;
  error.value = await launchBatch();
  launching.value = false;
}

// --- La file en cours ---
const downloading = computed(() => batchJob.value?.label.startsWith("Téléchargement") ?? false);
const phase = computed(() => {
  const label = batchJob.value?.label ?? "";
  if (downloading.value) return "Téléchargement des modèles… (une seule fois, puis ils restent sur cet ordinateur)";
  if (/^(Recherche|Suivi)/.test(label)) return "Recherche de vos objets dans la vidéo.";
  return "Analyse de la vidéo, image par image.";
});
const left = computed(() => {
  const j = batchJob.value;
  if (!j || downloading.value || j.fps <= 0 || j.done <= 0) return "";
  const s = Math.round((j.total - j.done) / j.fps);
  if (s > 5400) return `environ ${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
  if (s > 50) return `environ ${Math.max(1, Math.round(s / 60))} min`;
  return `${Math.max(1, s)} s`;
});
type Row = { name: string; status: "ok" | "failed" | "passed" | "current" | "waiting"; text: string };
/** Où en est chaque vidéo de la file lancée depuis cette page (vide si la liste n'est plus connue). */
const rows = computed<Row[]>(() => {
  const j = batchJob.value;
  const item = j?.item;
  if (!j || !item || batch.launched.length !== item[1]) return [];
  return batch.launched.map((name, i) => {
    const r = j.results?.[i];
    if (i < item[0] - 1) return r ? { name, status: r.ok ? "ok" : "failed", text: resultText(r) } : { name, status: "passed", text: "Traitée" };
    return i === item[0] - 1 ? { name, status: "current", text: "En cours" } : { name, status: "waiting", text: "En attente" };
  });
});
function cancel(): void {
  if (!confirming.value) {
    confirming.value = true;
    return;
  }
  confirming.value = false;
  void send("POST", "/api/cancel");
}

// --- Le résultat ---
const okCount = computed(() => batch.last?.results.filter((r) => r.ok).length ?? 0);
const allOk = computed(() => !!batch.last && batch.last.state === "done" && okCount.value === batch.last.total && batch.last.total > 0);
/** « 3 vidéos analysées sur 4 », « Aucune vidéo analysée sur 3 ». */
const countText = computed(() => {
  const total = batch.last?.total ?? 0;
  return okCount.value ? `${plural(okCount.value, "vidéo analysée", "vidéos analysées")} sur ${total}` : `Aucune vidéo analysée sur ${total}`;
});
const resultTitle = computed(() => {
  const last = batch.last;
  if (!last) return "";
  if (last.state === "error") return "La file n'a pas pu se terminer";
  if (last.state === "cancelled") return "File arrêtée";
  if (allOk.value) return last.total > 1 ? `Les ${last.total} vidéos sont analysées` : "La vidéo est analysée";
  return countText.value;
});
const resultLead = computed(() => {
  const last = batch.last;
  if (!last) return "";
  const parts: string[] = [];
  if (last.state === "cancelled") parts.push(`${countText.value}. Ce qui est fait est gardé.`);
  if (last.at) parts.push(`Terminée ${dayText(last.at / 1000)} à ${new Date(last.at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}.`);
  return parts.join(" ");
});
/** Résultat d'une vidéo, en clair. Le détail technique d'une erreur reste dans l'infobulle. */
function resultText(r: BatchResult): string {
  if (r.ok) {
    const notes = r.message.replace(/^Analysée\.\s*/, "");
    return nb(`Analysée${r.seconds >= 1 ? ` en ${took(r.seconds)}` : ""}.${notes ? ` ${notes}` : ""}`);
  }
  const text = r.message.replace(/^Erreur : /, "");
  if (/^vidéo illisible/.test(text)) return "Vidéo illisible : le fichier est abîmé, ou ce n'est pas une vidéo.";
  if (/^fichier introuvable/.test(text)) return "Fichier introuvable : il a été déplacé ou renommé.";
  return nb(text.charAt(0).toUpperCase() + text.slice(1));
}
/** Une analyse arrêtée en route a son projet : on peut l'ouvrir pour la reprendre. */
function canOpen(r: BatchResult): boolean {
  return r.ok || (/\.json$/i.test(r.path) && r.message.startsWith("Analyse interrompue"));
}
/** Vidéos de la file que l'arrêt a empêché d'analyser. */
const skipped = computed(() => {
  const last = batch.last;
  if (!last || last.total <= last.results.length) return { names: [] as string[], count: 0 };
  const names = batch.launched.length === last.total ? batch.launched.slice(last.results.length) : [];
  return { names, count: last.total - last.results.length };
});
function openResult(r: BatchResult): void {
  if (busy.value) return;
  batch.open = false;
  emit("open", r.path);
}

function close(): void {
  confirming.value = false;
  batch.open = false;
}

// --- Clavier : focus retenu dans le dialogue, Échap le ferme (la file continue) ---
function focusables(): HTMLElement[] {
  return [...(box.value?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), [tabindex='0']") ?? [])];
}
function onKey(event: KeyboardEvent): void {
  if (picking.value) return; // le sélecteur de fichiers a ses propres touches
  if (event.key === "Escape") {
    event.stopPropagation();
    event.preventDefault();
    if (confirming.value) confirming.value = false;
    else close();
  } else if (event.key === "Tab") {
    const list = focusables();
    if (!list.length) return;
    const index = list.indexOf(document.activeElement as HTMLElement);
    const next = event.shiftKey ? (index <= 0 ? list.length - 1 : index - 1) : index === list.length - 1 || index < 0 ? 0 : index + 1;
    event.preventDefault();
    list[next].focus();
  } else if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase())) {
    event.stopPropagation(); // « Annuler » ne défait pas une modification du projet derrière le dialogue
  }
}
/** Focus sur le bouton principal de la vue. Après le clic en cours : l'assistant retire le focus
 *  du bouton cliqué à la souris, et le ferait aussi du bouton qui vient de le recevoir. */
function focusMain(): void {
  void nextTick(() => window.setTimeout(() => (box.value?.querySelector<HTMLElement>("[data-main]:not(:disabled)") ?? box.value)?.focus(), 0));
}
onMounted(() => {
  opener = document.activeElement;
  window.addEventListener("keydown", onKey, true);
  confirming.value = batch.confirm && batchRunning.value;
  batch.confirm = false;
  focusMain();
});
watch(view, () => {
  confirming.value = false;
  picking.value = false;
  error.value = "";
  focusMain();
});
watch(() => batch.confirm, (ask) => {
  if (ask && batchRunning.value) confirming.value = true;
  batch.confirm = false;
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey, true);
  if (opener instanceof HTMLElement && document.contains(opener)) opener.focus({ preventScroll: true });
  else (document.activeElement as HTMLElement | null)?.blur?.();
});
</script>

<template>
  <div class="modal" @pointerdown.self="close">
    <div ref="box" class="dialog batch" :data-view="view" role="dialog" aria-modal="true" aria-labelledby="batch-title" tabindex="-1">
      <!-- La file en cours -->
      <template v-if="view === 'running'">
        <h3 id="batch-title"><span class="spinner"></span>Analyse de plusieurs vidéos</h3>
        <p class="batch-item" data-item role="status">{{ batchItemText }}</p>
        <div class="job-big" aria-live="off">
          <span class="job-percent mono">{{ batchPercent }}<small> %</small></span>
          <dl class="job-facts">
            <template v-if="batchJob && batchJob.fps > 0"><dt>Vitesse</dt><dd class="mono">{{ batchJob.fps }} i/s</dd></template>
            <template v-if="left"><dt>Reste pour cette vidéo</dt><dd class="mono">{{ left }}</dd></template>
          </dl>
        </div>
        <span class="bar tall"><span :style="{ transform: `scaleX(${batchPercent / 100})` }"></span></span>
        <p class="batch-phase">{{ phase }}</p>
        <ul v-if="rows.length" class="batch-list" aria-label="Vidéos de la file">
          <li v-for="(r, i) in rows" :key="i" :class="r.status" :aria-current="r.status === 'current' ? 'true' : undefined">
            <span v-if="r.status === 'current'" class="spinner small"></span>
            <Icon v-else :name="r.status === 'failed' ? 'alert' : r.status === 'waiting' ? 'clock' : 'check'" />
            <span class="batch-name" :title="r.name">{{ r.name }}</span>
            <span class="batch-state">{{ r.text }}</span>
          </li>
        </ul>
        <p class="note"><Icon name="info" /><span>Vous pouvez laisser l'ordinateur travailler toute la nuit. Gardez le logiciel ouvert et l'ordinateur allumé, sans mise en veille. Le résultat restera affiché ici.</span></p>
        <p v-if="batch.note" class="batch-note" role="status">{{ batch.note }}</p>
        <div v-if="confirming" class="row end confirm">
          <span class="grow">Arrêter la file ? Les vidéos déjà analysées sont gardées.</span>
          <button @click="confirming = false">Continuer</button>
          <button class="danger" data-stop @click="cancel">Oui, arrêter</button>
        </div>
        <div v-else class="row end">
          <button data-cancel @click="cancel">Annuler</button>
          <button class="primary" data-main data-reduce title="Fermer cette fenêtre sans arrêter la file (Échap)" @click="close">Réduire</button>
        </div>
      </template>

      <!-- Le résultat : il reste affiché jusqu'à « Nouvelle file » -->
      <template v-else-if="view === 'result' && batch.last">
        <h3 id="batch-title" :class="allOk ? 'done' : 'partial'"><Icon :name="allOk ? 'check' : 'alert'" :size="18" />{{ resultTitle }}</h3>
        <p v-if="resultLead" class="batch-lead">{{ resultLead }}</p>
        <div v-if="batch.last.state === 'error'" class="job-result error"><p>{{ batch.last.message }}</p></div>
        <ul v-if="batch.last.results.length || skipped.count" class="batch-list results" aria-label="Résultat de chaque vidéo">
          <li v-for="r in batch.last.results" :key="r.path" :class="r.ok ? 'ok' : 'failed'" data-result>
            <Icon :name="r.ok ? 'check' : 'alert'" />
            <span class="batch-what">
              <span class="batch-name" :title="r.path">{{ r.name }}</span>
              <small :title="r.ok ? undefined : r.message">{{ resultText(r) }}</small>
            </span>
            <button v-if="canOpen(r)" class="small" data-open :disabled="busy" :aria-label="`Ouvrir ${r.name}`" :title="r.ok ? 'Ouvrir cette vidéo pour la vérifier' : 'Ouvrir cette vidéo pour reprendre son analyse'" @click="openResult(r)">Ouvrir<Icon name="arrowRight" :size="14" /></button>
          </li>
          <li v-for="name in skipped.names" :key="'s' + name" class="waiting">
            <Icon name="clock" />
            <span class="batch-what"><span class="batch-name">{{ name }}</span><small>Pas analysée : la file a été arrêtée avant.</small></span>
          </li>
          <li v-if="skipped.count && !skipped.names.length" class="waiting">
            <Icon name="clock" />
            <span class="batch-what"><small>{{ skipped.count > 1 ? `${skipped.count} vidéos n'ont pas été analysées` : "1 vidéo n'a pas été analysée" }} : la file a été arrêtée avant.</small></span>
          </li>
        </ul>
        <p v-if="okCount" class="note"><Icon name="info" /><span>Ouvrez chaque vidéo pour la vérifier, puis créez sa version floutée. Cette liste reste ici ; vous retrouverez aussi ces vidéos dans les vidéos récentes.</span></p>
        <p v-if="batch.note" class="batch-note" role="status">{{ batch.note }}</p>
        <div class="row end">
          <button data-new :disabled="busy" title="Retirer ce résultat et préparer une autre file" @click="newBatch()"><Icon name="plus" />Nouvelle file</button>
          <span class="spacer"></span>
          <button class="primary" data-main data-close @click="close">Fermer</button>
        </div>
      </template>

      <!-- La liste à préparer -->
      <template v-else>
        <h3 id="batch-title"><Icon name="layers" :size="18" />Analyser plusieurs vidéos</h3>
        <p>Le logiciel analyse vos vidéos l'une après l'autre, sans vous. Lancez la file le soir : le matin, il ne reste qu'à les vérifier.</p>
        <ul v-if="batch.paths.length" class="batch-list" aria-label="Vidéos à analyser">
          <li v-for="(path, i) in batch.paths" :key="path">
            <span class="batch-rank mono">{{ i + 1 }}</span>
            <span class="batch-name" :title="path">{{ fileName(path) }}</span>
            <span class="batch-where" :title="path">{{ folderOf(path) }}</span>
            <button class="icon-btn bare" data-remove :aria-label="`Retirer ${fileName(path)} de la liste`" title="Retirer de la liste" @click="removeBatchPath(path)"><Icon name="close" :size="14" /></button>
          </li>
        </ul>
        <p v-else class="batch-empty">Aucune vidéo dans la liste pour l'instant.</p>
        <div class="row">
          <button data-add :class="{ primary: !batch.paths.length }" :data-main="batch.paths.length ? undefined : ''" @click="picking = true"><Icon name="plus" />Ajouter des vidéos…</button>
        </div>
        <p v-if="batch.note" class="batch-note" role="status">{{ batch.note }}</p>

        <label v-if="profile" class="check batch-profile">
          <input v-model="batch.profile" type="checkbox" data-profile aria-describedby="batch-profile-help" />
          <span>Reprendre mes réglages habituels<small id="batch-profile-help">Pour les vidéos neuves : flouter {{ profileText || "vos objets" }}{{ profile.objects.length ? ", que le logiciel cherchera tout seul" : "" }}.</small></span>
        </label>
        <div class="batch-type">
          <span id="batch-type-label" class="batch-type-label">Type de vidéo</span>
          <span class="joined" role="group" aria-labelledby="batch-type-label">
            <button
              v-for="id in choosablePresets" :key="id" :class="{ on: shownPreset === id }" :aria-pressed="shownPreset === id" :data-type="id"
              :disabled="useProfile" @click="batch.preset = id as BatchPreset"
            >{{ presetInfo[id].label }}</button>
          </span>
        </div>
        <div class="batch-type-text" role="status">
          <p v-if="useProfile" class="help">Type repris de vos réglages habituels{{ shownPreset === "perso" ? " (réglages modifiés à la main)" : "" }}.</p>
          <p v-else class="help">{{ nb(presetInfo[shownPreset].text) }}</p>
          <p v-if="presetInfo[shownPreset].warning" class="help batch-warning">{{ nb(presetInfo[shownPreset].warning) }}</p>
        </div>
        <p class="help">Une vidéo déjà analysée n'est pas analysée une seconde fois, sauf si son type change.</p>

        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <p v-else-if="busy" class="batch-note" role="status">Un autre traitement est en cours : attendez sa fin pour lancer la file.</p>
        <div class="row end">
          <button data-close @click="close">Fermer</button>
          <button class="primary" data-launch :data-main="batch.paths.length ? '' : undefined" :disabled="!batch.paths.length || busy || launching" @click="launch">
            <Icon name="scan" />{{ batch.paths.length > 1 ? `Lancer l'analyse des ${batch.paths.length} vidéos` : "Lancer l'analyse" }}
          </button>
        </div>
      </template>
    </div>

    <Transition name="modal">
      <FilePicker
        v-if="picking" multiple :simple="simple" :taken="batch.paths" :start="startDir" title="Ajouter des vidéos à la file"
        @pick-many="add" @close="picking = false"
      />
    </Transition>
  </div>
</template>

<style>
.batch { width: min(620px, 92vw); gap: 13px; font-size: 14px; overflow-y: auto; }
.batch:focus { outline: none; }
.batch > p { line-height: 1.5; }
.batch h3.done .icon { color: var(--face); }
.batch h3 .spinner { margin-right: 1px; }
.batch-item { margin-top: -4px; color: var(--text) !important; font-size: 16px; font-weight: 600; overflow-wrap: anywhere; }
.batch-phase, .batch-lead { margin-top: -4px; }
.batch-list { flex: none; margin: 0; padding: 4px; list-style: none; max-height: 34vh; overflow-y: auto; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; }
.batch-list li { display: flex; align-items: center; gap: 9px; min-height: 38px; padding: 5px 6px 5px 9px; }
.batch-list li + li { border-top: 1px solid var(--line); }
.batch-list li > .icon { flex: none; color: var(--dim); }
.batch-list li.ok > .icon { color: var(--face); }
.batch-list li.failed > .icon { color: var(--accent); }
.batch-list li.waiting, .batch-list li.passed { color: var(--dim); }
.batch-list li.waiting > .icon { color: var(--faint); }
.batch-list li.current { background: var(--accent-soft); border-radius: 5px; }
.batch-rank { flex: none; width: 20px; color: var(--faint); text-align: right; }
.batch-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); font-weight: 500; }
.batch-list li.waiting .batch-name, .batch-list li.passed .batch-name { color: var(--dim); font-weight: 400; }
.batch-where { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--faint); font-size: 12.5px; }
.batch-state { flex: none; margin-left: auto; color: var(--dim); font-size: 12.5px; }
.batch-list li.current .batch-state { color: var(--accent-hi); }
.batch-what { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.batch-what small { color: var(--dim); font-size: 12.5px; line-height: 1.4; overflow-wrap: anywhere; }
.batch-list li.failed .batch-what small { color: var(--accent-hi); }
.batch-list button.small { flex: none; height: 28px; padding: 0 10px; font-size: 13px; }
.batch-list .bare { flex: none; }
.batch-empty { padding: 18px 12px; background: var(--bg); border: 1px dashed var(--line-strong); border-radius: 8px; text-align: center; }
.batch-note { color: var(--accent-hi) !important; }
.batch label.batch-profile { align-items: flex-start; }
.batch .batch-profile input { margin-top: 2px; }
.batch-profile small { display: block; margin-top: 2px; color: var(--dim); font-size: 12.5px; line-height: 1.4; }
.batch-type { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.batch-type-label { color: var(--dim); }
.batch-type button { height: 32px; padding: 0 12px; }
.batch .help { margin-top: -5px; }
.batch-type-text { display: flex; flex-direction: column; gap: 2px; margin-top: -5px; }
.batch-type-text .help { margin-top: 0; }
.batch .batch-warning { color: var(--accent-hi); }
.batch .row button { height: 34px; padding: 0 14px; }
.batch .row.end { align-items: center; }
.batch .confirm button { flex: none; }
</style>
