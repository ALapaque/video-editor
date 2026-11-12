<script setup lang="ts">
// Panneau latéral : vérification guidée, zones de l'image courante, réglages.
import { computed, nextTick, reactive, ref, watch } from "vue";
import type { Track } from "../api";
import { applyProfile, presetInfo, saveProfile } from "../extras";
import { pending } from "../review";
import Icon from "./Icon.vue";
import PresetChoice from "./PresetChoice.vue";
import ReviewPanel from "./ReviewPanel.vue";
import {
  busy, current, deleteTrack, droppedHere, formatTime, kindLabel, notify, patchTrack, project, removeMute, saveManual,
  send, start, state, tracksNow,
} from "../store";

const tab = computed({ get: () => state.tab, set: (value) => (state.tab = value) });
const tabIndex = computed(() => ["review", "zones", "settings"].indexOf(tab.value));

// --- Zones de l'image courante ---
const sourceTime = computed(() => (current.value ? state.frame / current.value.fps : 0));
// zones floutées (ou désactivées à la main) à l'image courante ; les détections écartées par le
// score minimal sont listées à part
const tracksHere = computed<Track[]>(() =>
  tracksNow.value.filter((tr) => !tr.below_min_score).sort((a, b) => Number(a.disabled) - Number(b.disabled) || a.id - b.id),
);

// --- Objets désignés à la main dans le clip affiché (compteur, téléphone…) ---
const objects = computed(() => {
  const s = current.value;
  const out = new Map<string, { name: string; tracked: boolean; contour: boolean; length: number; start: number; end: number }>();
  for (const t of s ? (project.value?.tracks[s.clip.id] ?? []) : []) {
    if (t.kind !== "dash") continue;
    const o = out.get(t.name) ?? { name: t.name, tracked: false, contour: false, length: 0, start: t.start, end: t.end };
    o.tracked = o.tracked || t.source === "tracker";
    o.contour = o.contour || t.contour === true;
    if (t.source === "tracker") o.length += t.end - t.start;
    o.start = Math.min(o.start, t.start);
    o.end = Math.max(o.end, t.end);
    out.set(t.name, o);
  }
  return [...out.values()];
});
const confirmDelete = ref(""); // nom de l'objet dont la suppression attend une confirmation
async function clearObject(name: string): Promise<void> {
  confirmDelete.value = "";
  if (current.value) await send("POST", "/api/dash", { clip: current.value.clip.id, label: name, mode: "clear" });
}

// --- Contour précis d'un objet suivi (si cette installation le permet) ---
const canContour = computed(() => state.data.features?.contour === true);
const contourAsk = ref(""); // objet (« o:nom ») ou flou suivi (« t:numéro ») dont le calcul attend une confirmation
/** Durée du calcul annoncée avant de le lancer : deux à quatre fois la durée suivie. */
function contourTime(seconds: number): string {
  const s = Math.max(1, seconds) * 3;
  return s < 90 ? `environ ${Math.max(5, Math.round(s / 5) * 5)} s` : `environ ${Math.round(s / 60)} min`;
}
async function startContour(target: { label: string } | { id: number }): Promise<void> {
  contourAsk.value = "";
  if (current.value) await start("/api/contour", { clip: current.value.clip.id, ...target });
}
async function clearContour(target: { label: string } | { id: number }): Promise<void> {
  if (current.value && (await send("POST", "/api/contour", { clip: current.value.clip.id, ...target, clear: true }))) notify("Forme simple rétablie.");
}

// une zone sélectionnée ailleurs (clic dans l'image, flou qui vient d'être ajouté) est amenée à l'écran dans la liste
watch(() => [state.selectedTrack?.id, tab.value, contourAsk.value], () => {
  if (state.selectedTrack && tab.value === "zones") {
    void nextTick(() => document.querySelector(".zones li.selected")?.scrollIntoView({ block: "nearest" }));
  }
});

function isSelected(tr: Track): boolean {
  return state.selectedTrack?.id === tr.id && state.selectedTrack.clip === current.value?.clip.id;
}
function select(tr: Track): void {
  if (current.value) state.selectedTrack = { clip: current.value.clip.id, id: tr.id };
}
async function extend(tr: Track, side: "extend_before_s" | "extend_after_s", delta: number): Promise<void> {
  const general = Number(project.value?.settings.blur.extend_s ?? 0.3);
  const value = Math.max(0, Math.round(((tr[side] ?? general) + delta) * 100) / 100);
  await patchTrack(current.value!.clip.id, tr.id, { [side]: value });
}
async function setEdge(tr: Track, edge: "start" | "end"): Promise<void> {
  if (!tr.keyframes || !current.value) return;
  const t = sourceTime.value;
  const frames = tr.keyframes.map((k) => [...k]);
  const inside = edge === "start" ? frames.filter((k) => k[0] > t) : frames.filter((k) => k[0] < t);
  const anchor = edge === "start" ? frames[0] : frames[frames.length - 1];
  if (!inside.length) {
    notify(edge === "start" ? "Le début doit être avant la fin de la zone." : "La fin doit être après le début de la zone.");
    return;
  }
  const moved = [t, ...anchor.slice(1)];
  await saveManual(current.value.clip.id, edge === "start" ? [moved, ...inside] : [...inside, moved], tr.id);
}
function redraw(tr: Track): void {
  select(tr);
  state.tool = "redraw";
}
const mutesList = computed(() => (project.value?.mutes ?? []).map((m, index) => ({ ...m, index })));

// --- Réglages ---
const blur = reactive<Record<string, string | number | boolean>>({});
const analysis = reactive<Record<string, string | number | boolean | string[]>>({});
watch(() => project.value?.settings, (settings) => {
  if (!settings) return;
  Object.assign(blur, settings.blur);
  Object.assign(analysis, settings.analysis);
}, { immediate: true, deep: true });
// Scores minimaux : appliqués en direct pendant le glissement (envoi regroupé toutes les 200 ms).
// Tant qu'un envoi est en attente, l'état reçu du moteur ne remet pas le curseur en arrière.
const minScore = reactive({ face: 0.4, plate: 0.25, hits: 3 });
let minTimer = 0;
let minDirtyUntil = 0;
watch(() => project.value?.settings.blur, (b) => {
  if (!b || Date.now() < minDirtyUntil) return;
  minScore.face = Number(b.face_min_score ?? 0.4);
  minScore.plate = Number(b.plate_min_score ?? 0.25);
  minScore.hits = Number(b.min_hits ?? 3);
}, { immediate: true, deep: true });
function setMinScore(): void {
  minDirtyUntil = Date.now() + 700;
  window.clearTimeout(minTimer);
  minTimer = window.setTimeout(() => {
    void send("PUT", "/api/settings", { blur: { face_min_score: minScore.face, plate_min_score: minScore.plate, min_hits: minScore.hits } });
  }, 200);
}
function droppedCount(kind: string): number {
  const s = current.value;
  return s ? (project.value?.tracks[s.clip.id] ?? []).filter((t) => t.kind === kind && t.below_min_score).length : 0;
}
function plural(n: number, one: string, many: string): string {
  return `${n} ${n > 1 ? many : one}`;
}

async function saveBlur(): Promise<void> {
  await send("PUT", "/api/settings", { blur: { ...blur } });
}
async function saveAnalysis(): Promise<void> {
  const { kinds: _kinds, ...rest } = analysis;
  if (await send("PUT", "/api/settings", { analysis: rest })) notify("Réglages d'analyse enregistrés. Relancez « Analyser » : toute l'analyse sera refaite.");
}

// Modèles proposés : ceux que le moteur annonce (fournis, puis personnels), sinon la liste d'origine.
// Le modèle du projet reste dans la liste même s'il n'est plus annoncé.
function models(kind: "face" | "plate", fallback: string[]): string[] {
  const announced = state.data.models?.[kind];
  const list = announced?.length ? announced : fallback;
  const used = String(analysis[`${kind}_model`] ?? "");
  return used && !list.includes(used) ? [...list, used] : list;
}
const faceModels = computed(() => models("face", ["yolo11n-face", "yolo11s-face", "yolo11m-face"]));
const plateModels = computed(() => models("plate", ["yolo-v9-t-640", "yolo-v9-s-608"]));

// --- Profil « ma moto » ---
const profile = computed(() => state.data.profile ?? null);
const profileAsk = ref<"" | "save" | "apply">("");
const profileDate = computed(() =>
  profile.value ? new Date(profile.value.saved_at * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) : "",
);
const profileKinds = computed(() => (profile.value?.kinds ?? []).map((k) => `${kindLabel[k] ?? k}s`).join(", "));
function askSaveProfile(): void {
  if (profile.value) profileAsk.value = "save"; // un profil existe : on confirme avant de le remplacer
  else void saveProfile();
}
async function confirmProfile(): Promise<void> {
  const what = profileAsk.value;
  profileAsk.value = "";
  if (what === "save") await saveProfile();
  else if (what === "apply") await applyProfile();
}
</script>

<template>
  <aside class="side">
    <nav class="tabs" role="tablist">
      <button role="tab" :aria-selected="tab === 'review'" :class="{ on: tab === 'review' }" @click="tab = 'review'">Vérification <b v-if="pending">{{ pending }}</b></button>
      <button role="tab" :aria-selected="tab === 'zones'" :class="{ on: tab === 'zones' }" @click="tab = 'zones'">Zones</button>
      <button role="tab" :aria-selected="tab === 'settings'" :class="{ on: tab === 'settings' }" @click="tab = 'settings'">Réglages</button>
      <span class="tab-ind" :style="{ transform: `translateX(${tabIndex * 100}%)` }"></span>
    </nav>

    <Transition name="pane" mode="out-in">
      <ReviewPanel v-if="tab === 'review'" key="review" />

      <section v-else-if="tab === 'zones'" key="zones" class="pane">
        <div class="row pair">
          <button :class="{ on: state.tool === 'manual' }" :disabled="busy" title="Tracer un rectangle à flouter sur l'image courante" @click="state.tool = state.tool === 'manual' ? 'none' : 'manual'">+ Zone manuelle</button>
          <button :class="{ on: state.tool === 'dash' }" :disabled="busy" title="Désigner sur l'image courante un objet à flouter : compteur, téléphone, GPS…" @click="state.tool = state.tool === 'dash' ? 'none' : 'dash'"><Icon name="target" />Suivre un objet…</button>
        </div>
        <template v-if="objects.length">
          <h4 class="plain">Objets suivis</h4>
          <ul class="list objects">
            <li v-for="o in objects" :key="o.name">
              <span class="dot dash"></span>
              <span class="grow"><strong>{{ o.name }}</strong> <span class="dim">· {{ o.tracked ? "suivi" : "zone fixe" }}</span><small>source <span class="mono">{{ formatTime(o.start, 1) }} → {{ formatTime(o.end, 1) }}</span></small></span>
              <template v-if="confirmDelete === o.name">
                <button class="small danger" :disabled="busy" @click="clearObject(o.name)">Confirmer</button>
                <button class="small" @click="confirmDelete = ''">Annuler</button>
              </template>
              <button v-else class="small danger" :disabled="busy" :title="`Supprimer toutes les zones de « ${o.name} » dans ce clip`" @click="confirmDelete = o.name">Supprimer</button>
              <div v-if="canContour && o.tracked" class="object-more">
                <template v-if="contourAsk === `o:${o.name}`">
                  <p>Le calcul est long : {{ contourTime(o.length) }} pour cet objet. La première fois, un modèle de 155 Mo est téléchargé.</p>
                  <button class="small attention" :disabled="busy" @click="startContour({ label: o.name })">Lancer le calcul</button>
                  <button class="small" @click="contourAsk = ''">Annuler</button>
                </template>
                <template v-else-if="o.contour">
                  <span class="tag ok"><Icon name="check" :size="12" />contour précis</span>
                  <button class="small" :disabled="busy" title="Le flou redevient un rectangle arrondi autour de l'objet" @click="clearContour({ label: o.name })">Revenir à la forme simple</button>
                </template>
                <button v-else class="small" :disabled="busy" title="Le flou épouse la forme de l'objet au lieu de le couvrir d'un rectangle. Calcul long." @click="contourAsk = `o:${o.name}`">Contour précis</button>
              </div>
            </li>
          </ul>
        </template>
        <h4 v-if="objects.length" class="plain">Zones de cette image</h4>
        <p class="help">Zones actives sur l'image affichée. Cliquez une zone dans l'image pour la sélectionner.</p>
        <p v-if="!tracksHere.length" class="blank">Aucune zone sur cette image.</p>
        <ul class="list zones">
          <li v-for="tr in tracksHere" :key="tr.id" :class="{ selected: isSelected(tr), off: tr.disabled }" @click="select(tr)">
            <div class="head">
              <span class="dot" :class="tr.kind"></span>
              <strong>{{ tr.name }}</strong>
              <span class="dim">n° {{ tr.id }}<template v-if="tr.source === 'auto'"> · score {{ tr.score.toFixed(2) }}</template><template v-else-if="tr.kind === 'dash' && tr.source === 'manual'"> · zone fixe</template><template v-else-if="tr.source === 'manual'"> · manuelle</template><template v-else> · suivi</template></span>
              <span v-if="tr.forced" class="tag" title="Floutée bien que son score soit sous le minimum">forcée</span>
            </div>
            <div class="dim span">source <span class="mono">{{ formatTime(tr.start, 1) }} → {{ formatTime(tr.end, 1) }}</span></div>
            <div v-if="isSelected(tr)" class="actions">
              <template v-if="tr.source === 'manual'">
                <button :disabled="busy" @click.stop="setEdge(tr, 'start')">Début = cette image</button>
                <button :disabled="busy" @click.stop="setEdge(tr, 'end')">Fin = cette image</button>
                <button :disabled="busy" title="Retracer le rectangle sur l'image courante : la zone se déplacera progressivement jusqu'ici" @click.stop="redraw(tr)">Déplacer ici…</button>
                <button class="danger" :disabled="busy" @click.stop="deleteTrack(current!.clip.id, tr.id)"><Icon name="trash" />Supprimer</button>
              </template>
              <template v-else>
                <div class="stepper"><span class="stepper-label">Avant</span>
                  <button :disabled="busy" aria-label="Réduire la prolongation avant" @click.stop="extend(tr, 'extend_before_s', -0.5)">−</button>
                  <span class="mono">{{ (tr.extend_before_s ?? Number(blur.extend_s)).toFixed(1) }} s</span>
                  <button :disabled="busy" aria-label="Augmenter la prolongation avant" @click.stop="extend(tr, 'extend_before_s', 0.5)">+</button>
                </div>
                <div class="stepper"><span class="stepper-label">Après</span>
                  <button :disabled="busy" aria-label="Réduire la prolongation après" @click.stop="extend(tr, 'extend_after_s', -0.5)">−</button>
                  <span class="mono">{{ (tr.extend_after_s ?? Number(blur.extend_s)).toFixed(1) }} s</span>
                  <button :disabled="busy" aria-label="Augmenter la prolongation après" @click.stop="extend(tr, 'extend_after_s', 0.5)">+</button>
                </div>
                <button v-if="!tr.disabled" class="danger" :disabled="busy" title="Faux positif : ne pas flouter cette zone" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: true })">Ne pas flouter</button>
                <button v-else :disabled="busy" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: false })">Rétablir le flou</button>
                <button v-if="tr.forced" :disabled="busy" title="Revenir à la règle du score minimal pour cette détection" @click.stop="patchTrack(current!.clip.id, tr.id, { forced: false })">Ne plus forcer</button>
                <button v-if="tr.source === 'tracker'" class="danger" :disabled="busy" @click.stop="deleteTrack(current!.clip.id, tr.id)"><Icon name="trash" />Supprimer le suivi</button>
                <!-- flou ajouté qui suit : contour précis, comme pour un objet suivi -->
                <template v-if="canContour && tr.source === 'tracker' && tr.kind === 'manual'">
                  <div v-if="contourAsk === `t:${tr.id}`" class="object-more" @click.stop>
                    <p>Le calcul est long : {{ contourTime(tr.end - tr.start) }} pour ce flou. La première fois, un modèle de 155 Mo est téléchargé.</p>
                    <button class="small attention" :disabled="busy" @click="startContour({ id: tr.id })">Lancer le calcul</button>
                    <button class="small" @click="contourAsk = ''">Annuler</button>
                  </div>
                  <button v-else-if="tr.contour" :disabled="busy" title="Le flou redevient un rectangle arrondi" @click.stop="clearContour({ id: tr.id })">Revenir à la forme simple</button>
                  <button v-else :disabled="busy" title="Le flou épouse la forme de ce qui est suivi. Calcul long." @click.stop="contourAsk = `t:${tr.id}`">Contour précis</button>
                </template>
              </template>
            </div>
          </li>
        </ul>
        <!-- Détections écartées par le score minimal : repliées par défaut ; ouvertes, elles sont entourées en gris sur la vidéo -->
        <div class="fold" :class="{ open: state.showDropped }">
          <button class="fold-head" :aria-expanded="state.showDropped" @click="state.showDropped = !state.showDropped">
            <Icon :name="state.showDropped ? 'chevronDown' : 'chevronRight'" :size="14" />Détections écartées <b class="count">{{ droppedHere.length }}</b>
          </button>
          <template v-if="state.showDropped">
            <p class="help">Détections dont le meilleur score est sous le minimum (onglet « Réglages ») : elles ne sont pas floutées. Elles sont entourées en gris sur l'image.</p>
            <p v-if="!droppedHere.length" class="dim">Aucune sur cette image.</p>
            <ul class="list dropped">
              <li v-for="tr in droppedHere" :key="tr.id">
                <span class="dot off"></span>
                <span class="grow"><strong>{{ tr.name }}</strong> <span class="dim">n° {{ tr.id }} · meilleur score {{ tr.best_score.toFixed(2) }}</span></span>
                <button class="small" :disabled="busy" title="Flouter cette détection malgré son score" @click="patchTrack(current!.clip.id, tr.id, { forced: true })">Flouter quand même</button>
              </li>
            </ul>
          </template>
        </div>

        <template v-if="mutesList.length">
          <h4>Passages muets</h4>
          <ul class="list mutes">
            <li v-for="m in mutesList" :key="m.index">
              <Icon name="volumeOff" />
              <span class="grow">{{ m.clip }} · source <span class="mono">{{ formatTime(m.start, 1) }} → {{ formatTime(m.end, 1) }}</span></span>
              <button class="danger" @click="removeMute(m.index)">Retirer</button>
            </li>
          </ul>
        </template>
      </section>

      <section v-else key="settings" class="pane settings">
        <h4>Flou</h4>
        <p class="help">Appliqué au prochain rendu, sans refaire l'analyse.</p>
        <label>Marge autour des zones <input v-model.number="blur.margin" type="number" min="0" max="2" step="0.05" @change="saveBlur" /></label>
        <label>Prolongation avant / après (s) <input v-model.number="blur.extend_s" type="number" min="0" max="5" step="0.1" @change="saveBlur" /></label>
        <label>Type de flou
          <select v-model="blur.style" @change="saveBlur"><option value="pixelate">mosaïque lissée</option><option value="solid">aplat de couleur</option></select>
        </label>
        <label>Forme pour les visages
          <select v-model="blur.face_shape" @change="saveBlur"><option value="ellipse">ellipse</option><option value="rounded">rectangle arrondi</option><option value="rect">rectangle</option></select>
        </label>
        <label>Forme pour les plaques
          <select v-model="blur.plate_shape" @change="saveBlur"><option value="rounded">rectangle arrondi</option><option value="rect">rectangle</option><option value="ellipse">ellipse</option></select>
        </label>
        <label>Stabiliser les flous <input v-model="blur.smooth" type="checkbox" aria-describedby="smooth-help" @change="saveBlur" /></label>
        <p id="smooth-help" class="help">Le flou ne tremble plus et ne change plus de taille à chaque image.</p>
        <div class="slider-row">
          <label for="min-face">Score minimal des visages <span class="mono value">{{ minScore.face.toFixed(2) }}</span></label>
          <input id="min-face" v-model.number="minScore.face" type="range" min="0" max="0.9" step="0.05" :disabled="busy" @input="setMinScore" />
          <small class="dim">{{ plural(droppedCount("face"), "détection écartée", "détections écartées") }} dans ce clip</small>
        </div>
        <div class="slider-row">
          <label for="min-plate">Score minimal des plaques <span class="mono value">{{ minScore.plate.toFixed(2) }}</span></label>
          <input id="min-plate" v-model.number="minScore.plate" type="range" min="0" max="0.9" step="0.05" :disabled="busy" @input="setMinScore" />
          <small class="dim">{{ plural(droppedCount("plate"), "détection écartée", "détections écartées") }} dans ce clip</small>
        </div>
        <div class="slider-row">
          <label for="min-hits">Images sûres exigées <span class="mono value">{{ minScore.hits }}</span></label>
          <input id="min-hits" v-model.number="minScore.hits" type="range" min="1" max="8" step="1" :disabled="busy" @input="setMinScore" />
        </div>
        <p class="help">Une détection n'est floutée que si elle atteint ce score minimal sur ce nombre d'images (une seule suffit si elle est très nette). Les fausses détections clignotent ; les vrais visages et plaques restent à l'écran. Plus haut : moins de faux flous, mais un vrai visage aperçu très brièvement peut être manqué.</p>
        <template v-if="project?.mismatch">
          <h4>Clips de formats différents</h4>
          <label>Aligner sur
            <select :value="project.conform" @change="send('PUT', '/api/settings', { conform: ($event.target as HTMLSelectElement).value })">
              <option value="" disabled>à choisir</option>
              <option value="first">le premier clip</option>
              <option value="largest">la plus grande résolution et fréquence</option>
            </select>
          </label>
        </template>
        <h4>Détection</h4>
        <PresetChoice />
        <p class="help">Réglages fins, pour les cas particuliers. Modifier ces réglages refait toute l'analyse.</p>
        <label>Modèle de visages
          <select v-model="analysis.face_model"><option v-for="m in faceModels" :key="m">{{ m }}</option></select>
        </label>
        <label>Modèle de plaques
          <select v-model="analysis.plate_model"><option v-for="m in plateModels" :key="m">{{ m }}</option></select>
        </label>
        <label>Score minimal pour suivre un objet <input v-model.number="analysis.conf_new" type="number" min="0.01" max="0.9" step="0.05" /></label>
        <label>Taille des tuiles (0 = auto, -1 = aucune) <input v-model.number="analysis.tile_size" type="number" min="-1" step="64" /></label>
        <label>Détecter une image sur <input v-model.number="analysis.detect_every" type="number" min="1" max="10" step="1" /></label>
        <button :disabled="busy" @click="saveAnalysis">Enregistrer les réglages de détection</button>
        <button class="wrap" :disabled="busy" title="Enregistre dans un dossier les images des plaques vérifiées de ce projet, pour entraîner un modèle de plaques" @click="start('/api/dataset')">Exporter des images d'entraînement (plaques)</button>
        <p class="help">Pour améliorer la détection des plaques avec vos propres vidéos. Le dossier créé est indiqué à la fin.</p>

        <h4>Profil « ma moto »</h4>
        <p class="help">Le profil garde les réglages de ce projet et l'aspect de vos objets suivis (compteur, téléphone…), pour les retrouver dans vos prochaines vidéos.</p>
        <div v-if="profile" class="profile-card">
          <Icon name="user" :size="18" />
          <span class="grow">
            <strong>Profil enregistré le {{ profileDate }}</strong>
            <small>{{ profile.objects.length ? `Objets : ${profile.objects.join(", ")}` : "Aucun objet suivi" }}</small>
            <small>Détection : {{ profileKinds || "aucune" }} · préréglage {{ presetInfo[profile.preset]?.label ?? profile.preset }}</small>
          </span>
        </div>
        <p v-else class="blank">Aucun profil enregistré pour l'instant.</p>
        <div v-if="profileAsk" class="profile-ask" role="alert">
          <p v-if="profileAsk === 'save'">Le profil enregistré le {{ profileDate }} sera remplacé par les réglages et les objets de ce projet.</p>
          <p v-else>Les réglages de ce projet seront remplacés par ceux du profil, puis vos objets seront recherchés dans la vidéo. Si le préréglage d'analyse change, l'analyse sera à refaire.</p>
          <span class="row">
            <button class="small attention" :disabled="busy" @click="confirmProfile">{{ profileAsk === "save" ? "Remplacer le profil" : "Appliquer le profil" }}</button>
            <button class="small" @click="profileAsk = ''">Annuler</button>
          </span>
        </div>
        <template v-else>
          <button class="wrap" :disabled="busy" @click="askSaveProfile">Enregistrer ce projet comme profil</button>
          <button class="wrap" :disabled="busy || !profile" :title="profile ? 'Reprendre les réglages du profil et rechercher vos objets dans cette vidéo' : 'Enregistrez d\'abord un profil'" @click="profileAsk = 'apply'">Appliquer mon profil à ce projet</button>
        </template>
      </section>
    </Transition>
  </aside>
</template>
