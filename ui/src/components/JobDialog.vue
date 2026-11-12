<script setup lang="ts">
// Dialogue de progression : il s'ouvre au lancement d'un traitement (aperçu, analyse, suivi,
// export), se réduit à l'indicateur de la ligne de guide, et montre le résultat à la fin.
// Le traitement continue qu'il soit ouvert ou réduit.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { job, project, send, state } from "../store";
import Icon from "./Icon.vue";

const emit = defineEmits<{ review: [] }>();
const box = ref<HTMLDivElement>();
const confirming = ref(false);
let opener: Element | null = null;

const j = computed(() => job.value!);
const running = computed(() => j.value.state === "running");
const percent = computed(() => Math.min(100, Math.round((100 * j.value.done) / Math.max(1, j.value.total))));
const downloading = computed(() => j.value.label.startsWith("Téléchargement"));
const eta = computed(() => {
  if (j.value.fps <= 0) return "";
  const s = Math.round((j.value.total - j.value.done) / j.value.fps);
  if (s > 90) return `environ ${Math.round(s / 60)} min`;
  return `${s} s`;
});
/** Nom de l'objet suivi, tiré du libellé « Suivi : nom ». */
const objectName = computed(() => j.value.label.match(/^Suivi : (.+)$/)?.[1] ?? "");
const title = computed(() => {
  switch (j.value.kind) {
    case "proxy": return "Préparation de l'aperçu";
    case "analyze": return "Analyse du montage";
    case "dash": return objectName.value ? `Suivi de l'objet « ${objectName.value} »` : "Suivi d'un objet";
    case "render": return "Export de la vidéo";
    default: return "Traitement en cours";
  }
});
/** Étape en cours, en clair : les identifiants de clip sont remplacés par le nom du fichier. */
const phase = computed(() => {
  if (downloading.value) return `${j.value.label} (une seule fois, puis il reste sur cette machine)`;
  if (j.value.kind === "dash") return "Recherche de l'objet image par image, vers l'avant et l'arrière.";
  if (j.value.kind === "render") return "Écriture de la vidéo floutée.";
  const named = j.value.label.replace(/\bc\d+\b/g, (id) => project.value?.clips.find((c) => c.id === id)?.name ?? id);
  return named.replace(/^Analyse /, "Détection des visages et des plaques : ");
});
const after = computed(() => {
  switch (j.value.kind) {
    case "proxy": return "Ensuite, la lecture sera fluide. Ce n'est fait qu'une fois par vidéo.";
    case "analyze": return "L'analyse terminée, vous pourrez vérifier le floutage.";
    case "dash": return "Le suivi terminé, l'objet sera flouté sur tout le clip ; regardez le résultat en lecture.";
    case "render": return "La vidéo sera écrite dans le dossier du projet. Vos fichiers d'origine ne sont pas modifiés.";
    default: return "";
  }
});
const needsConfirm = computed(() => j.value.kind === "analyze" || j.value.kind === "render");

// --- Résultat ---
const resultTitle = computed(() => {
  if (j.value.state === "error") return "Le traitement a échoué";
  if (j.value.state === "cancelled") return "Traitement annulé";
  switch (j.value.kind) {
    case "analyze": return j.value.message.startsWith("Analyse terminée") ? "Analyse terminée" : "Analyse interrompue";
    case "render": return "Export terminé";
    case "dash": return "Suivi terminé";
    default: return "Traitement terminé";
  }
});
/** Chemin du fichier écrit par l'export, pour le montrer à part (texte sélectionnable). */
const outputPath = computed(() => (j.value.state === "done" ? (j.value.message.match(/^Vidéo écrite : (.+)$/s)?.[1] ?? "") : ""));
/** Message du moteur, une information par ligne (vitesse, accélération utilisée…). */
const lines = computed(() => {
  if (outputPath.value) return ["La vidéo a été écrite ici :"];
  const text = j.value.message || (j.value.state === "cancelled" ? "Traitement annulé." : "");
  return text.split(/\n| — /).map((l) => l.trim()).filter((l) => l && l !== `${resultTitle.value}.`).map((l) => l.replace(/^Analyse terminée\.\s*/, ""));
});
const canReview = computed(() => j.value.kind === "analyze" && j.value.state === "done" && j.value.message.startsWith("Analyse terminée"));

function close(): void {
  confirming.value = false;
  state.jobOpen = false;
  if (!running.value) state.jobFinished = false; // résultat lu
}
function cancel(): void {
  if (needsConfirm.value && !confirming.value) {
    confirming.value = true;
    return;
  }
  confirming.value = false;
  void send("POST", "/api/cancel");
}
function review(): void {
  close();
  emit("review");
}

// --- Clavier : focus retenu dans le dialogue tant qu'il est ouvert, rendu à la fermeture ---
function focusables(): HTMLElement[] {
  return [...(box.value?.querySelectorAll<HTMLElement>("button:not(:disabled), [tabindex='0']") ?? [])];
}
function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.stopPropagation();
    if (confirming.value) confirming.value = false;
    else close();
  } else if (event.key === "Tab") {
    const list = focusables();
    if (!list.length) return;
    const index = list.indexOf(document.activeElement as HTMLElement);
    const next = event.shiftKey ? (index <= 0 ? list.length - 1 : index - 1) : index === list.length - 1 || index < 0 ? 0 : index + 1;
    event.preventDefault();
    list[next].focus();
  }
}
function focusMain(): void {
  void nextTick(() => (box.value?.querySelector<HTMLElement>("[data-main]") ?? box.value)?.focus());
}
onMounted(() => {
  opener = document.activeElement;
  window.addEventListener("keydown", onKey, true);
  if (state.jobConfirm) confirming.value = needsConfirm.value;
  state.jobConfirm = false;
  focusMain();
});
watch(running, focusMain); // passage à l'écran de résultat : le bouton principal change
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey, true);
  if (opener instanceof HTMLElement && document.contains(opener)) opener.focus({ preventScroll: true });
  else (document.activeElement as HTMLElement | null)?.blur?.();
});
</script>

<template>
  <div class="modal" @pointerdown.self="close">
    <div ref="box" class="dialog job-dialog" role="dialog" aria-modal="true" aria-labelledby="job-title" tabindex="-1">
      <!-- Traitement en cours -->
      <template v-if="running">
        <h3 id="job-title"><span class="spinner"></span>{{ title }}</h3>
        <p class="job-phase">{{ phase }}</p>
        <div class="job-big" role="status" aria-live="off">
          <span class="job-percent mono">{{ percent }}<small> %</small></span>
          <dl class="job-facts">
            <template v-if="!downloading"><dt>Images</dt><dd class="mono">{{ j.done }} / {{ j.total }}</dd></template>
            <template v-if="j.fps > 0"><dt>Vitesse</dt><dd class="mono">{{ j.fps }} i/s</dd></template>
            <template v-if="eta"><dt>Temps restant</dt><dd class="mono">{{ eta }}</dd></template>
          </dl>
        </div>
        <span class="bar tall"><span :style="{ transform: `scaleX(${percent / 100})` }"></span></span>
        <p class="note"><Icon name="info" />{{ after }} Vous pouvez réduire cette fenêtre : le traitement continue.</p>
        <div v-if="confirming" class="row end confirm">
          <span class="grow">{{ j.kind === "render" ? "Arrêter l'export ? La vidéo en cours d'écriture sera abandonnée." : "Arrêter l'analyse ? Ce qui est déjà analysé est conservé." }}</span>
          <button @click="confirming = false">Continuer</button>
          <button class="danger" @click="cancel">Oui, arrêter</button>
        </div>
        <div v-else class="row end">
          <button @click="cancel">Annuler</button>
          <button class="primary" data-main title="Fermer cette fenêtre sans arrêter le traitement (Échap)" @click="close">Réduire</button>
        </div>
      </template>

      <!-- Résultat -->
      <template v-else>
        <h3 id="job-title" :class="j.state"><Icon :name="j.state === 'done' ? 'check' : 'alert'" :size="18" />{{ resultTitle }}</h3>
        <div class="job-result" :class="j.state">
          <p v-for="l in lines" :key="l">{{ l }}</p>
          <p v-if="outputPath" class="job-path mono" tabindex="0">{{ outputPath }}</p>
        </div>
        <div class="row end">
          <template v-if="canReview">
            <button @click="close">Fermer</button>
            <button class="primary" data-main @click="review">Commencer la vérification<Icon name="arrowRight" /></button>
          </template>
          <button v-else class="primary" data-main @click="close">{{ j.kind === "dash" && j.state === "done" ? "Voir le résultat" : "Fermer" }}</button>
        </div>
      </template>
    </div>
  </div>
</template>
