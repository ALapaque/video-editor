<script setup lang="ts">
// Panneau latéral : vérification, flous de l'image affichée (et endroits à ne jamais flouter), réglages.
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import type { Track, Tune } from "../api";
import { applyProfile, applyTune, autoKinds, MANUAL_WARNING, manualOnly, presetInfo, removeIgnoreZone, saveProfile, setAutoKinds, setBlurSpan, spanEditable, tuneGain, tuneSuggestion } from "../extras";
import { byDoubt, displayName, kindFilters, matchesKind, panels, placeOf, type KindFilter } from "../blurs";
import { pending } from "../review";
import BlurRow from "./BlurRow.vue";
import Icon from "./Icon.vue";
import PresetChoice from "./PresetChoice.vue";
import ReviewPanel from "./ReviewPanel.vue";
import {
  busy, clipById, current, deleteTrack, droppedHere, kindLabel, needsAnalysis, notify, patchTrack, project, removeMute,
  send, start, state, trackById, tracksNow,
} from "../store";

const tab = computed({ get: () => state.tab, set: (value) => (state.tab = value) });
const tabIndex = computed(() => ["review", "zones", "settings"].indexOf(tab.value));

/** « 0:12 » : temps du clip, sans décimales. */
function clock(x: number): string {
  return `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`;
}
function span(a: number, b: number): string {
  return clock(a) === clock(b) ? `à ${clock(a)}` : `de ${clock(a)} à ${clock(b)}`;
}

// --- Flous de l'image affichée ---
const sourceTime = computed(() => (current.value ? state.frame / current.value.fps : 0));
const filter = ref<KindFilter>("all");
// flous appliqués (ou retirés à la main) à l'image affichée, du plus douteux au plus sûr ; ceux
// qui sont trop peu sûrs pour être floutés sont listés à part
const tracksHere = computed<Track[]>(() => {
  const list = tracksNow.value.filter((tr) => !tr.below_min_score);
  // Le flou posé à la main qui est sélectionné reste dans la liste même hors de l'image affichée :
  // c'est depuis une autre image qu'on règle son début ou sa fin.
  const sel = state.selectedTrack;
  if (sel && sel.clip === current.value?.clip.id && !list.some((t) => t.id === sel.id)) {
    const kept = trackById(sel.clip, sel.id);
    if (kept && spanEditable(kept)) list.push(kept);
  }
  return list.sort((a, b) => Number(a.disabled) - Number(b.disabled) || byDoubt(a, b));
});
/** Le flou est-il présent à l'image affichée ? (Un flou posé à la main sélectionné peut ne pas l'être.) */
function hereNow(tr: Track): boolean {
  return tracksNow.value.some((t) => t.id === tr.id);
}
const shownHere = computed(() => tracksHere.value.filter((tr) => matchesKind(tr, filter.value)));
function countOf(f: KindFilter): number {
  return tracksHere.value.filter((tr) => matchesKind(tr, f)).length;
}

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

// --- Endroits à ne jamais flouter dans le clip affiché ---
// Là où le logiciel se trompe toujours (rétroviseur, autocollant, cadran pris pour un visage) :
// on trace un rectangle, et plus aucun flou automatique n'y est posé, même après une nouvelle analyse.
const ignoreZones = computed(() => {
  const c = current.value?.clip;
  return c ? (project.value?.ignore?.[c.id] ?? []).map((box) => ({
    box, place: placeOf(box, c.width, c.height),
    share: Math.max(1, Math.round((100 * (box[2] - box[0]) * (box[3] - box[1])) / Math.max(1, c.width * c.height))),
  })) : [];
});
function toggleManual(): void {
  const on = state.tool === "manual" && !panels.ignoring;
  panels.ignoring = false;
  state.tool = on ? "none" : "manual";
}
function toggleIgnore(): void {
  if (panels.ignoring) {
    panels.ignoring = false;
    state.tool = "none";
  } else {
    state.tool = "manual"; // même tracé qu'un flou ajouté ; le lecteur en fait une zone à ne jamais flouter
    panels.ignoring = true;
  }
}
async function dropIgnore(index: number): Promise<void> {
  const c = current.value?.clip;
  if (!c) return;
  const off = () => (project.value?.tracks[c.id] ?? []).filter((t) => t.disabled).length;
  const before = off();
  const zones = project.value?.ignore?.[c.id]?.length ?? 0;
  panels.ignoreHover = null;
  await removeIgnoreZone(c.id, index);
  if ((project.value?.ignore?.[c.id]?.length ?? 0) >= zones) return; // refusé : le moteur a dit pourquoi
  const n = before - off();
  notify(n > 0 ? `Cet endroit peut de nouveau être flouté. ${n > 1 ? `${n} flous remis` : "1 flou remis"}.` : "Cet endroit peut de nouveau être flouté.");
}
onBeforeUnmount(() => {
  panels.ignoreHover = null;
  if (panels.ignoring) toggleIgnore();
});

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

// un flou sélectionné ailleurs (clic dans l'image, flou qui vient d'être ajouté) est amené à l'écran dans la liste
watch(() => [state.selectedTrack?.id, tab.value, contourAsk.value], () => {
  if (state.selectedTrack && tab.value === "zones") {
    const sel = state.selectedTrack;
    const track = tracksHere.value.find((t) => t.id === sel.id);
    if (track && !matchesKind(track, filter.value)) filter.value = "all"; // sinon il resterait caché par le filtre
    void nextTick(() => document.querySelector(".zones li.selected")?.scrollIntoView({ block: "nearest" }));
  }
});

function isSelected(tr: Track): boolean {
  return state.selectedTrack?.id === tr.id && state.selectedTrack.clip === current.value?.clip.id;
}
function select(tr: Track): void {
  if (current.value) state.selectedTrack = { clip: current.value.clip.id, id: tr.id };
}
// survol d'un flou de la liste : il ressort sur la vidéo
function hover(tr: Track | null): void {
  const s = current.value;
  state.focus = tr && s ? { clip: s.clip.id, ids: [tr.id] } : null;
}
watch(tab, () => {
  state.focus = null;
  panels.ignoreHover = null;
  if (panels.ignoring) toggleIgnore(); // l'outil ne reste pas armé hors de l'onglet « Flous »
});
onBeforeUnmount(() => (state.focus = null));

async function extend(tr: Track, side: "extend_before_s" | "extend_after_s", delta: number): Promise<void> {
  const general = Number(project.value?.settings.blur.extend_s ?? 0.3);
  const value = Math.max(0, Math.round(((tr[side] ?? general) + delta) * 100) / 100);
  await patchTrack(current.value!.clip.id, tr.id, { [side]: value });
}
/** « Début = cette image » / « Fin = cette image » d'un flou posé à la main. Un flou fixe se
 *  rallonge ou se raccourcit ; un flou qui suit ce qu'on a entouré ne peut qu'être raccourci. */
async function setEdge(tr: Track, edge: "start" | "end"): Promise<void> {
  const s = current.value;
  if (!s) return;
  const t = sourceTime.value;
  const half = 0.5 / s.fps;
  if (edge === "start" && t > tr.end - half) return notify("Le début doit être avant la fin du flou.");
  if (edge === "end" && t < tr.start + half) return notify("La fin doit être après le début du flou.");
  if (tr.source === "tracker" && (t < tr.start - half || t > tr.end + half)) {
    return notify("Ce flou suit ce que vous avez entouré : il ne peut qu'être raccourci. Placez-vous sur une image où il est présent.");
  }
  await setBlurSpan(s.clip.id, tr.id, edge === "start" ? t : tr.start, edge === "end" ? t : tr.end);
}
/** « 0:12,4 » : instant du clip, au dixième de seconde. */
function precise(x: number): string {
  const tenths = Math.round(x * 10);
  return `${Math.floor(tenths / 600)}:${String(Math.floor((tenths % 600) / 10)).padStart(2, "0")},${tenths % 10}`;
}
function redraw(tr: Track): void {
  select(tr);
  state.tool = "redraw";
}
function seconds(x: number): string {
  return `${x.toFixed(1).replace(".", ",")} s`;
}
const mutesList = computed(() => (project.value?.mutes ?? []).map((m, index) => ({ ...m, index, name: clipById(m.clip)?.name ?? m.clip })));

// --- Flous automatiques : visages, plaques, ou aucun (tout se pose à la main) ---
// Retirer une catégorie fait disparaître ses flous automatiques : on le dit et on demande avant.
const kindsAsk = ref<string[] | null>(null); // choix en attente de confirmation
const shownKinds = computed(() => kindsAsk.value ?? autoKinds.value);
/** Catégories qui disparaîtraient avec le choix en attente. */
const kindsGone = computed(() => autoKinds.value.filter((k) => !(kindsAsk.value ?? autoKinds.value).includes(k)));
/** « des visages et des plaques », « les plaques »… */
function kindWords(kinds: string[], article: "des" | "les"): string {
  return kinds.map((k) => `${article} ${k === "face" ? "visages" : "plaques"}`).join(" et ");
}
function hasAutoBlurs(kind: string): boolean {
  return Object.values(project.value?.tracks ?? {}).some((list) => list.some((t) => t.source === "auto" && t.kind === kind));
}
/** Coche ou décoche une catégorie ("none" : les deux d'un coup). */
function toggleKind(kind: "face" | "plate" | "none", on: boolean): void {
  const wanted = kind === "none" ? [] : ["face", "plate"].filter((k) => (k === kind ? on : shownKinds.value.includes(k)));
  if (wanted.join() === autoKinds.value.join()) return void (kindsAsk.value = null); // retour au choix du projet
  if (autoKinds.value.some((k) => !wanted.includes(k) && hasAutoBlurs(k))) kindsAsk.value = wanted; // des flous vont disparaître
  else void applyKinds(wanted);
}
async function applyKinds(wanted: string[]): Promise<void> {
  kindsAsk.value = null;
  if (!(await setAutoKinds(wanted))) return; // refusé : le message est affiché
  if (!wanted.length) notify("Plus aucun flou automatique : vous posez tous les flous à la main. Pour revenir en arrière : Annuler (Ctrl+Z).");
  else if (needsAnalysis.value) notify(`Choix enregistré. Relancez « Analyser » : l'analyse est à refaire pour ${kindWords(wanted, "les")}.`);
}
watch(() => project.value?.path, () => (kindsAsk.value = null));

// --- Réglages ---
const blur = reactive<Record<string, string | number | boolean>>({});
const analysis = reactive<Record<string, string | number | boolean | string[]>>({});
watch(() => project.value?.settings, (settings) => {
  if (!settings) return;
  Object.assign(blur, settings.blur);
  Object.assign(analysis, settings.analysis);
}, { immediate: true, deep: true });
/** Marge autour des flous, en pour cent de leur taille. */
const marginPct = computed({
  get: () => Math.round(Number(blur.margin ?? 0) * 100),
  set: (value: number) => (blur.margin = Math.round(value) / 100),
});
/** Contre-vérification des objets douteux : activée tant que le moteur ne dit pas le contraire. */
const confirmDoubt = computed({
  get: () => blur.confirm !== false,
  set: (value: boolean) => (blur.confirm = value),
});
const extendS = computed({
  get: () => Number(blur.extend_s ?? 0.3),
  set: (value: number) => (blur.extend_s = Math.round(value * 10) / 10),
});

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
function droppedCount(kind?: string): number {
  const s = current.value;
  return s ? (project.value?.tracks[s.clip.id] ?? []).filter((t) => (!kind || t.kind === kind) && t.below_min_score).length : 0;
}
function plural(n: number, one: string, many: string): string {
  return `${n} ${n > 1 ? many : one}`;
}

// Curseur unique « Moins de faux flous ↔ Moins de risques » : cinq crans autour des scores
// minimaux du préréglage. À droite, les scores baissent (on floute davantage, au risque de faux
// flous) ; à gauche, ils montent. Les curseurs détaillés restent dans les réglages experts.
const SENS_FACE = 0.1;
const SENS_PLATE = 0.05;
const sensBase = computed(() => (project.value?.preset === "difficile" ? { face: 0.3, plate: 0.18 } : { face: 0.4, plate: 0.25 }));
const sensSteps = ["Très peu de faux flous", "Peu de faux flous", "Équilibré (conseillé)", "Prudent", "Très prudent"];
function round2(x: number): number {
  return Math.round(x * 100) / 100;
}
/** Cran correspondant aux scores minimaux actuels (null : réglés à la main dans les réglages experts). */
const sensNow = computed<number | null>(() => {
  const v = Math.round((sensBase.value.face - minScore.face) / SENS_FACE);
  if (Math.abs(v) > 2) return null;
  const face = round2(sensBase.value.face - v * SENS_FACE);
  const plate = round2(sensBase.value.plate - v * SENS_PLATE);
  return Math.abs(face - minScore.face) < 0.011 && Math.abs(plate - minScore.plate) < 0.011 ? v : null;
});
const sens = ref(0);
watch(sensNow, (v) => v !== null && (sens.value = v), { immediate: true });
function setSens(): void {
  const v = sens.value;
  minScore.face = Math.min(0.9, Math.max(0.05, round2(sensBase.value.face - v * SENS_FACE)));
  minScore.plate = Math.min(0.9, Math.max(0.05, round2(sensBase.value.plate - v * SENS_PLATE)));
  setMinScore();
}

// « Régler d'après mes corrections » : les flous retirés sont des erreurs du logiciel. Le moteur
// propose (sans rien réanalyser) les réglages qui n'en produiraient plus le plus grand nombre,
// sans perdre un seul flou gardé. La proposition est dite en clair ; les noms des réglages et
// leurs valeurs ne sont que dans l'infobulle.
const tune = ref<Tune | null>(null);
const tuneAsking = ref(false);
const tuneNames: Record<string, string> = { face_min_score: "score minimal des visages", plate_min_score: "score minimal des plaques", min_hits: "images sûres exigées" };
/** Détail technique de la proposition : « score minimal des visages : 0,40 → 0,55 ; … ». */
const tuneDetail = computed(() => {
  const now = project.value?.settings.blur ?? {};
  const show = (key: string, v: unknown) => (key === "min_hits" ? String(v) : Number(v).toFixed(2).replace(".", ","));
  return Object.entries(tune.value?.settings ?? {})
    .map(([key, value]) => `${tuneNames[key] ?? key} : ${show(key, now[key])} → ${show(key, value)}`)
    .join(" ; ");
});
async function askTune(): Promise<void> {
  if (tuneAsking.value) return;
  tuneAsking.value = true;
  const found = await tuneSuggestion();
  tuneAsking.value = false;
  if (!found) return notify("La proposition n'a pas pu être calculée.", "error");
  tune.value = found;
  void nextTick(() => document.querySelector<HTMLElement>(".tune-ask [data-tune-main]")?.focus());
}
async function acceptTune(): Promise<void> {
  const found = tune.value;
  if (!found?.usable || busy.value) return;
  if (!(await applyTune(found))) return; // refusé : le message est affiché
  notify("Réglages appliqués d'après vos corrections. Pour revenir en arrière : Annuler (Ctrl+Z).");
}
// la proposition vaut pour le projet tel qu'il était : au moindre changement, elle s'efface
watch(() => [state.data.version, project.value?.path], () => (tune.value = null));

async function saveBlur(): Promise<void> {
  await send("PUT", "/api/settings", { blur: { ...blur } });
}
async function saveAnalysis(): Promise<void> {
  const { kinds: _kinds, ...rest } = analysis;
  if (await send("PUT", "/api/settings", { analysis: rest })) notify("Réglages d'analyse enregistrés. Relancez « Analyser » : toute l'analyse sera refaite.");
}
const expert = ref(false); // réglages experts : repliés par défaut
/** Analyse fine (par tuiles) sur une image sur N, telle qu'elle est enregistrée dans le projet. */
const tileEvery = computed(() => Number(project.value?.settings.analysis.tile_every ?? 1));

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
      <button role="tab" :aria-selected="tab === 'zones'" :class="{ on: tab === 'zones' }" @click="tab = 'zones'">Flous</button>
      <button role="tab" :aria-selected="tab === 'settings'" :class="{ on: tab === 'settings' }" @click="tab = 'settings'">Réglages</button>
      <span class="tab-ind" :style="{ transform: `translateX(${tabIndex * 100}%)` }"></span>
    </nav>

    <Transition name="pane" mode="out-in">
      <ReviewPanel v-if="tab === 'review'" key="review" />

      <section v-else-if="tab === 'zones'" key="zones" class="pane">
        <div class="row pair">
          <button :class="{ on: state.tool === 'manual' && !panels.ignoring }" :disabled="busy" title="Tracer un rectangle à flouter sur l'image affichée" @click="toggleManual"><Icon name="plus" />Ajouter un flou</button>
          <button :class="{ on: state.tool === 'dash' }" :disabled="busy" title="Désigner sur l'image affichée un objet à flouter : compteur, téléphone, GPS…" @click="state.tool = state.tool === 'dash' ? 'none' : 'dash'"><Icon name="target" />Suivre un objet…</button>
        </div>
        <button
          class="ignore-tool" :class="{ on: panels.ignoring }" :aria-pressed="panels.ignoring" :disabled="busy"
          title="Tracer un rectangle là où le logiciel se trompe toujours (rétroviseur, autocollant…) : plus aucun flou automatique n'y sera posé dans ce clip"
          @click="toggleIgnore"
        ><Icon name="ban" />Ne jamais flouter ici…</button>
        <!-- l'avertissement, une seule fois : pendant le tracé, puis au-dessus de la liste -->
        <p v-if="panels.ignoring || ignoreZones.length" class="note warn ignore-note">
          <Icon name="alert" />
          <span v-if="panels.ignoring">Tracez un rectangle sur l'endroit où le logiciel se trompe toujours : rétroviseur, autocollant, cadran… <strong>Un visage ou une plaque qui se tiendrait dans cette zone ne sera pas flouté.</strong></span>
          <span v-else>{{ ignoreZones.length > 1 ? "À ces endroits" : "À cet endroit" }}, le logiciel ne pose plus de flou dans ce clip, même après une nouvelle analyse. <strong>Un visage ou une plaque qui s'y tiendrait ne sera pas flouté.</strong></span>
        </p>
        <template v-if="ignoreZones.length">
          <h4 class="plain">Endroits jamais floutés</h4>
          <ul class="list ignores" @mouseleave="panels.ignoreHover = null">
            <li v-for="(z, i) in ignoreZones" :key="z.box.join()" :class="{ here: panels.ignoreHover === i }" @mouseenter="panels.ignoreHover = i">
              <i class="sw sw-ignore" aria-hidden="true"></i>
              <span class="grow"><strong>Endroit {{ i + 1 }}</strong> <span class="dim">· {{ z.place }}</span><small>{{ z.share <= 1 ? "environ 1 %" : `${z.share} %` }} de l'image</small></span>
              <button
                class="small danger" :disabled="busy" :title="`Les flous que cet endroit écartait reviennent`" :aria-label="`Retirer l'endroit ${i + 1} (${z.place}) : il pourra de nouveau être flouté`"
                @focus="panels.ignoreHover = i" @blur="panels.ignoreHover = null" @click="dropIgnore(i)"
              >Retirer</button>
            </li>
          </ul>
        </template>
        <template v-if="objects.length">
          <h4 class="plain">Objets suivis</h4>
          <ul class="list objects">
            <li v-for="o in objects" :key="o.name">
              <span class="dot dash"></span>
              <span class="grow"><strong>{{ o.name }}</strong> <span class="dim">· {{ o.tracked ? "suivi" : "flou fixe" }}</span><small>{{ span(o.start, o.end) }} dans ce clip</small></span>
              <template v-if="confirmDelete === o.name">
                <button class="small danger" :disabled="busy" @click="clearObject(o.name)">Confirmer</button>
                <button class="small" @click="confirmDelete = ''">Annuler</button>
              </template>
              <button v-else class="small danger" :disabled="busy" :title="`Supprimer tous les flous de « ${o.name} » dans ce clip`" @click="confirmDelete = o.name">Supprimer</button>
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
        <h4 class="plain">Flous de cette image</h4>
        <div v-if="tracksHere.length" class="joined kind-filter" role="group" aria-label="Type de flou à afficher">
          <button
            v-for="f in kindFilters" :key="f.id" class="small" :class="{ on: filter === f.id }" :aria-pressed="filter === f.id"
            :disabled="f.id !== 'all' && !countOf(f.id)" @click="filter = f.id"
          >{{ f.label }}<span class="mono kind-count">{{ countOf(f.id) }}</span></button>
        </div>
        <p class="help">Du plus douteux au plus sûr. Cliquez un flou ici ou dans l'image pour le régler.</p>
        <p v-if="!tracksHere.length" class="blank">Aucun flou sur cette image.</p>
        <ul class="list zones">
          <li
            v-for="tr in shownHere" :key="tr.id" :data-id="tr.id" :class="{ selected: isSelected(tr), off: tr.disabled }" tabindex="0"
            :aria-label="`${displayName(tr)}${tr.disabled ? ', retiré' : ''}`" :aria-current="isSelected(tr) ? 'true' : undefined"
            @click="select(tr)" @keydown.enter.self="select(tr)" @keydown.space.self.prevent="select(tr)" @mouseenter="hover(tr)" @mouseleave="hover(null)"
          >
            <div class="head">
              <BlurRow :clip="current!.clip.id" :track="tr" defer>
                <span class="dim span">
                  <template v-if="tr.disabled">retiré</template>
                  <template v-else-if="tr.kind === 'dash' && tr.source === 'manual'">flou fixe</template>
                  <template v-else-if="tr.source === 'manual'">ajouté à la main</template>
                  <template v-else-if="tr.source === 'tracker' && tr.reacquired">retrouvé par le logiciel</template>
                  <template v-else-if="tr.source === 'tracker'">suit ce que vous avez entouré</template>
                  <span v-if="tr.forced" class="tag" title="Flouté bien que le logiciel soit peu sûr">flouté quand même</span>
                  <span v-if="!hereNow(tr)" class="tag" title="Ce flou n'est pas présent à l'image affichée. Il reste listé tant qu'il est sélectionné, pour régler son début ou sa fin.">pas sur cette image</span>
                </span>
              </BlurRow>
              <!-- morceau qu'une ancienne recherche automatique a « retrouvé » : une supposition, à retirer d'un clic si elle est fausse -->
              <button
                v-if="tr.reacquired && !tr.disabled" class="small danger" data-reacquired :disabled="busy"
                title="Le logiciel a cru retrouver l'objet ici, tout seul, après une coupure ou une perte. S'il s'est trompé d'objet, retirez ce flou."
                :aria-label="`Retirer le flou ${displayName(tr)}, retrouvé par le logiciel`" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: true })"
              >Retirer</button>
            </div>
            <div v-if="isSelected(tr)" class="actions">
              <!-- flou posé à la main : son début et sa fin, réglés d'après l'image affichée -->
              <p v-if="spanEditable(tr)" class="span-line" data-span>
                Commence à <span class="mono">{{ precise(tr.start) }}</span>, finit à <span class="mono">{{ precise(tr.end) }}</span>
                <small v-if="tr.source === 'tracker'">Ce flou suit ce que vous avez entouré : il ne peut qu'être raccourci.</small>
              </p>
              <template v-if="tr.source === 'manual'">
                <button :disabled="busy" data-span-start title="Le flou commence à l'image affichée" @click.stop="setEdge(tr, 'start')">Début = cette image</button>
                <button :disabled="busy" data-span-end title="Le flou finit à l'image affichée" @click.stop="setEdge(tr, 'end')">Fin = cette image</button>
                <button :disabled="busy" title="Retracer le rectangle sur l'image affichée : le flou se déplacera progressivement jusqu'ici" @click.stop="redraw(tr)">Déplacer ici…</button>
                <button class="danger" :disabled="busy" @click.stop="deleteTrack(current!.clip.id, tr.id)"><Icon name="trash" />Supprimer</button>
              </template>
              <template v-else>
                <template v-if="spanEditable(tr)">
                  <button :disabled="busy || !hereNow(tr)" data-span-start title="Le flou commence à l'image affichée (il ne peut qu'être raccourci)" @click.stop="setEdge(tr, 'start')">Début = cette image</button>
                  <button :disabled="busy || !hereNow(tr)" data-span-end title="Le flou finit à l'image affichée (il ne peut qu'être raccourci)" @click.stop="setEdge(tr, 'end')">Fin = cette image</button>
                </template>
                <div class="stepper" title="Le flou commence un peu avant que l'objet soit vu"><span class="stepper-label">Avant</span>
                  <button :disabled="busy" aria-label="Commencer le flou plus tard" @click.stop="extend(tr, 'extend_before_s', -0.5)">−</button>
                  <span class="mono">{{ seconds(tr.extend_before_s ?? Number(blur.extend_s)) }}</span>
                  <button :disabled="busy" aria-label="Commencer le flou plus tôt" @click.stop="extend(tr, 'extend_before_s', 0.5)">+</button>
                </div>
                <div class="stepper" title="Le flou reste un peu après que l'objet a disparu"><span class="stepper-label">Après</span>
                  <button :disabled="busy" aria-label="Finir le flou plus tôt" @click.stop="extend(tr, 'extend_after_s', -0.5)">−</button>
                  <span class="mono">{{ seconds(tr.extend_after_s ?? Number(blur.extend_s)) }}</span>
                  <button :disabled="busy" aria-label="Finir le flou plus tard" @click.stop="extend(tr, 'extend_after_s', 0.5)">+</button>
                </div>
                <button v-if="!tr.disabled" class="danger" :disabled="busy" title="Faux flou : ne pas flouter ceci" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: true })">Ne pas flouter</button>
                <button v-else :disabled="busy" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: false })">Rétablir le flou</button>
                <button v-if="tr.forced" :disabled="busy" title="Le flou suit de nouveau la règle normale : il n'est appliqué que si le logiciel est assez sûr" @click.stop="patchTrack(current!.clip.id, tr.id, { forced: false })">Ne plus flouter quand même</button>
                <button v-if="tr.source === 'tracker'" class="danger" :disabled="busy" @click.stop="deleteTrack(current!.clip.id, tr.id)"><Icon name="trash" />Supprimer ce flou</button>
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
        <!-- Non floutés (trop peu sûrs) : repliés par défaut ; ouverts, ils sont entourés en gris sur la vidéo -->
        <div class="fold" :class="{ open: state.showDropped }">
          <button class="fold-head" :aria-expanded="state.showDropped" @click="state.showDropped = !state.showDropped">
            <Icon :name="state.showDropped ? 'chevronDown' : 'chevronRight'" :size="14" />Non floutés (trop peu sûrs) <b class="count">{{ droppedHere.length }}</b>
          </button>
          <template v-if="state.showDropped">
            <p class="help">Le logiciel a cru voir quelque chose, mais pas assez nettement pour le flouter. C'est réglable avec le curseur « Moins de faux flous ↔ Moins de risques » (onglet « Réglages »). Ils sont entourés en gris sur l'image.</p>
            <p v-if="!droppedHere.length" class="dim">Aucun sur cette image.</p>
            <ul class="list dropped">
              <li v-for="tr in droppedHere" :key="tr.id" :data-id="tr.id" @mouseenter="hover(tr)" @mouseleave="hover(null)">
                <BlurRow class="grow" :clip="current!.clip.id" :track="tr" defer />
                <button class="small" :disabled="busy" title="Flouter ceci malgré le doute" @click="patchTrack(current!.clip.id, tr.id, { forced: true })">Flouter quand même</button>
              </li>
            </ul>
          </template>
        </div>

        <template v-if="mutesList.length">
          <h4>Son coupé</h4>
          <ul class="list mutes">
            <li v-for="m in mutesList" :key="m.index">
              <Icon name="volumeOff" />
              <span class="grow">{{ span(m.start, m.end) }} <span class="dim">· {{ m.name }}</span></span>
              <button class="danger" @click="removeMute(m.index)">Retirer</button>
            </li>
          </ul>
        </template>
      </section>

      <section v-else key="settings" class="pane settings">
        <!-- ce que le logiciel floute tout seul : visages, plaques, ou rien (tout se pose à la main) -->
        <h4>Flous automatiques</h4>
        <p class="help">Ce que le logiciel cherche et floute tout seul pendant l'analyse.</p>
        <label>Visages <input type="checkbox" data-auto="face" :checked="shownKinds.includes('face')" :disabled="busy" @change="toggleKind('face', ($event.target as HTMLInputElement).checked)" /></label>
        <label>Plaques <input type="checkbox" data-auto="plate" :checked="shownKinds.includes('plate')" :disabled="busy" @change="toggleKind('plate', ($event.target as HTMLInputElement).checked)" /></label>
        <div v-if="kindsAsk" class="profile-ask" role="alert" data-auto-ask>
          <p>Les flous automatiques {{ kindWords(kindsGone, "des") }} seront retirés du projet. Si vous recochez la case plus tard, l'analyse sera à refaire.{{ kindsAsk.length ? ` L'analyse est aussi à refaire pour ${kindWords(kindsAsk, "les")}.` : "" }}</p>
          <p v-if="!kindsAsk.length"><strong>{{ MANUAL_WARNING }}</strong></p>
          <span class="row">
            <button class="small attention" data-auto-confirm :disabled="busy" @click="applyKinds(kindsAsk)">Retirer ces flous</button>
            <button class="small" data-auto-cancel @click="kindsAsk = null">Annuler</button>
          </span>
        </div>
        <p v-else-if="manualOnly" class="note warn" data-auto-none>
          <Icon name="alert" /><span><strong>Aucun : je pose tout à la main.</strong> {{ MANUAL_WARNING }} Les flous s'ajoutent avec « Ajouter un flou », dans l'onglet « Flous » ou « Vérification ».</span>
        </p>
        <button v-else class="wrap" data-auto-off :disabled="busy" title="Plus rien n'est cherché automatiquement : seul ce que vous entourez est flouté. Évite les flous posés à tort (arbre, reflet…)." @click="toggleKind('none', false)">Aucun : je pose tout à la main</button>

        <h4>Flou</h4>
        <p class="help">Appliqué au prochain export, sans refaire l'analyse.</p>
        <label>Type de flou
          <select v-model="blur.style" @change="saveBlur"><option value="pixelate">mosaïque lissée</option><option value="solid">aplat de couleur</option></select>
        </label>
        <label>Forme pour les visages
          <select v-model="blur.face_shape" @change="saveBlur"><option value="ellipse">ellipse</option><option value="rounded">rectangle arrondi</option><option value="rect">rectangle</option></select>
        </label>
        <label>Forme pour les plaques
          <select v-model="blur.plate_shape" @change="saveBlur"><option value="rounded">rectangle arrondi</option><option value="rect">rectangle</option><option value="ellipse">ellipse</option></select>
        </label>
        <div class="slider-row">
          <label for="blur-margin">Marge autour des flous <span class="mono value">{{ marginPct }} %</span></label>
          <input id="blur-margin" v-model.number="marginPct" type="range" min="0" :max="Math.max(100, marginPct)" step="5" :aria-valuetext="`${marginPct} %`" @change="saveBlur" />
          <small class="dim">Le flou déborde de l'objet de {{ marginPct }} % de sa taille, de chaque côté.</small>
        </div>
        <div class="slider-row">
          <label for="blur-extend">Garder le flou avant et après <span class="mono value">{{ seconds(extendS) }}</span></label>
          <input id="blur-extend" v-model.number="extendS" type="range" min="0" :max="Math.max(2, extendS)" step="0.1" :aria-valuetext="seconds(extendS)" @change="saveBlur" />
          <small class="dim">Le flou apparaît un peu avant que l'objet soit vu et reste un peu après.</small>
        </div>
        <label>Stabiliser les flous <input v-model="blur.smooth" type="checkbox" aria-describedby="smooth-help" @change="saveBlur" /></label>
        <p id="smooth-help" class="help">Le flou ne tremble plus et ne change plus de taille à chaque image.</p>
        <template v-if="project?.settings.blur.confirm !== undefined">
          <label>Contre-vérifier les flous douteux <input v-model="confirmDoubt" type="checkbox" aria-describedby="confirm-help" @change="saveBlur" /></label>
          <p id="confirm-help" class="help">Un objet douteux que le logiciel ne retrouve pas en le revoyant de plus près n'est pas flouté (arbre, reflet…). Il reste dans la liste des non floutés.</p>
        </template>

        <h4>Ce qui est flouté</h4>
        <div class="slider-row sens">
          <label for="sensitivity" class="sens-ends"><span>Moins de faux flous</span><span aria-hidden="true">↔</span><span>Moins de risques</span></label>
          <input
            id="sensitivity" v-model.number="sens" type="range" min="-2" max="2" step="1" :disabled="busy"
            :aria-valuetext="sensSteps[sens + 2]" @input="setSens"
          />
          <small><strong>{{ sensNow === null ? "Réglage personnalisé" : sensSteps[sens + 2] }}</strong> · {{ plural(droppedCount(), "objet non flouté", "objets non floutés") }} dans ce clip, car trop peu sûrs.</small>
        </div>
        <p class="help">Vers la gauche : moins de flous inutiles (reflets, feuillage…), mais un visage aperçu très brièvement peut rester net. Vers la droite : rien n'échappe, au prix de quelques faux flous à retirer. Appliqué tout de suite, sans refaire l'analyse.</p>
        <button
          class="wrap" data-tune :disabled="busy || tuneAsking" :aria-expanded="!!tune"
          title="Le logiciel se sert des flous que vous avez retirés pour proposer un meilleur réglage. Rien n'est analysé à nouveau, et rien ne change sans votre accord."
          @click="askTune"
        ><Icon name="sliders" />Régler d'après mes corrections</button>
        <div v-if="tune" class="profile-ask tune-ask" data-tune-card role="status">
          <template v-if="tune.usable">
            <p :title="tuneDetail">Avec ces réglages, {{ tuneGain(tune) }}, sans perdre aucun flou que vous avez gardé.</p>
            <p v-if="tune.best.new" class="dim">{{ tune.best.new > 1 ? `${tune.best.new} objets que le logiciel n'avait pas floutés le seraient en plus` : "1 objet que le logiciel n'avait pas flouté le serait en plus" }} : regardez-les ensuite.</p>
            <span class="row">
              <button class="small attention" data-tune-main data-tune-apply :disabled="busy" :title="tuneDetail" @click="acceptTune">Appliquer</button>
              <button class="small" data-tune-no @click="tune = null">Non merci</button>
            </span>
          </template>
          <template v-else>
            <p>{{ tune.reason || "Vos corrections ne permettent pas de proposer un meilleur réglage." }}</p>
            <span class="row"><button class="small" data-tune-main data-tune-no @click="tune = null">Fermer</button></span>
          </template>
        </div>
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
        <h4>Analyse</h4>
        <PresetChoice />

        <div class="fold expert" :class="{ open: expert }">
          <button class="fold-head" :aria-expanded="expert" aria-controls="expert-settings" @click="expert = !expert">
            <Icon :name="expert ? 'chevronDown' : 'chevronRight'" :size="14" />Réglages experts
          </button>
          <div v-if="expert" id="expert-settings" class="expert-body">
            <p class="help">Pour les cas particuliers. Les réglages de flou s'appliquent tout de suite ; ceux de la détection obligent à refaire toute l'analyse.</p>
            <div class="slider-row">
              <label for="min-face">Score minimal des visages <span class="mono value">{{ minScore.face.toFixed(2) }}</span></label>
              <input id="min-face" v-model.number="minScore.face" type="range" min="0" max="0.9" step="0.05" :disabled="busy" @input="setMinScore" />
              <small class="dim">{{ plural(droppedCount("face"), "visage non flouté", "visages non floutés") }} dans ce clip</small>
            </div>
            <div class="slider-row">
              <label for="min-plate">Score minimal des plaques <span class="mono value">{{ minScore.plate.toFixed(2) }}</span></label>
              <input id="min-plate" v-model.number="minScore.plate" type="range" min="0" max="0.9" step="0.05" :disabled="busy" @input="setMinScore" />
              <small class="dim">{{ plural(droppedCount("plate"), "plaque non floutée", "plaques non floutées") }} dans ce clip</small>
            </div>
            <div class="slider-row">
              <label for="min-hits">Images sûres exigées <span class="mono value">{{ minScore.hits }}</span></label>
              <input id="min-hits" v-model.number="minScore.hits" type="range" min="1" max="8" step="1" :disabled="busy" @input="setMinScore" />
            </div>
            <p class="help">Un objet n'est flouté que s'il atteint ce score minimal sur ce nombre d'images (une seule suffit s'il est très net). Les faux flous clignotent ; les vrais visages et plaques restent à l'écran. Plus haut : moins de faux flous, mais un vrai visage aperçu très brièvement peut être manqué.</p>
            <template v-if="project?.settings.blur.dash_reacquire !== undefined">
              <h5>Objets suivis</h5>
              <label>Rechercher l'objet tout seul après une coupure <input v-model="blur.dash_reacquire" type="checkbox" data-reacquire aria-describedby="reacquire-help" :disabled="busy" @change="saveBlur" /></label>
              <p id="reacquire-help" class="help"><strong>Peut se tromper d'objet (un rétroviseur pris pour le compteur).</strong> Sans cette recherche, le flou s'arrête à la coupure ou quand l'objet est perdu, et une alerte vous demande de le remontrer. S'applique aux prochains suivis.</p>
            </template>
            <h5>Détection</h5>
            <label>Modèle de visages
              <select v-model="analysis.face_model"><option v-for="m in faceModels" :key="m">{{ m }}</option></select>
            </label>
            <label>Modèle de plaques
              <select v-model="analysis.plate_model"><option v-for="m in plateModels" :key="m">{{ m }}</option></select>
            </label>
            <label>Score minimal pour suivre un objet <input v-model.number="analysis.conf_new" type="number" min="0.01" max="0.9" step="0.05" /></label>
            <label>Taille des tuiles (0 = auto, -1 = aucune) <input v-model.number="analysis.tile_size" type="number" min="-1" step="64" /></label>
            <label>Analyser une image sur <input v-model.number="analysis.detect_every" type="number" min="1" max="10" step="1" /></label>
            <label>Analyse fine
              <select v-model.number="analysis.tile_every" data-tile-every aria-describedby="tile-every-help">
                <option :value="1">sur chaque image</option>
                <option :value="2">une image sur deux (plus rapide)</option>
                <option v-if="tileEvery > 2" :value="tileEvery">une image sur {{ tileEvery }}</option>
              </select>
            </label>
            <p id="tile-every-help" class="help">L'analyse fine cherche les petits objets lointains en découpant l'image en morceaux. Une image sur deux : analyse presque deux fois plus rapide, mais un petit objet lointain doit rester un peu plus longtemps à l'écran pour être vu. Comme les autres réglages de détection : à enregistrer ci-dessous, puis toute l'analyse est à refaire.</p>
            <button :disabled="busy" @click="saveAnalysis">Enregistrer les réglages de détection</button>
            <button class="wrap" :disabled="busy" title="Enregistre dans un dossier les images des plaques vérifiées de ce projet, pour entraîner un modèle de plaques" @click="start('/api/dataset')">Exporter des images d'entraînement (plaques)</button>
            <p class="help">Pour améliorer la reconnaissance des plaques avec vos propres vidéos. Le dossier créé est indiqué à la fin.</p>
          </div>
        </div>

        <h4>Profil « ma moto »</h4>
        <p class="help">Le profil garde les réglages de ce projet et l'aspect de vos objets suivis (compteur, téléphone…), pour les retrouver dans vos prochaines vidéos.</p>
        <div v-if="profile" class="profile-card">
          <Icon name="user" :size="18" />
          <span class="grow">
            <strong>Profil enregistré le {{ profileDate }}</strong>
            <small>{{ profile.objects.length ? `Objets : ${profile.objects.join(", ")}` : "Aucun objet suivi" }}</small>
            <small>À flouter : {{ profileKinds || "rien" }} · préréglage {{ presetInfo[profile.preset]?.label ?? profile.preset }}</small>
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

<style>
/* === 12 points : liste des flous, réglages en clair === */
.kind-filter { align-self: flex-start; }
.kind-filter button { gap: 5px; padding: 0 8px; }
.kind-count { color: var(--faint); font-size: 11px; }
.kind-filter button.on .kind-count { color: var(--accent-hi); }
.list.zones li { padding: 6px 8px 6px 6px; }
.zones li:focus-visible { outline-offset: -2px; }
.list.zones .head { align-items: flex-start; }
.zones .head .blur-row { flex: 1; }
.zones .span .tag { margin-left: 4px; }
.dropped li .blur-row { flex: 1; }
.list.dropped li { padding: 5px 6px; }
.settings .slider-row small strong { color: var(--text); font-weight: 600; }
.pane .sens-ends { display: flex; justify-content: space-between; gap: 8px; min-height: 22px; color: var(--text); }
.sens-ends span[aria-hidden] { color: var(--faint); }
.fold.expert { padding-top: 6px; border-top: 1px solid var(--line); }
.fold.expert .fold-head { margin-left: -6px; font-weight: 600; }
.expert-body { display: flex; flex-direction: column; gap: 9px; animation: fade-in var(--m-panel) var(--ease); }
.expert-body h5 { margin: 6px 0 0; font-size: 12.5px; font-weight: 650; color: var(--text); }
/* régler d'après mes corrections : la proposition, en clair */
.tune-ask p[title] { cursor: help; }
.tune-ask p.dim { color: var(--dim); }

/* flou posé à la main sélectionné : son début et sa fin */
.zones .actions .span-line { flex: 1 0 100%; margin: 0; color: var(--dim); font-size: 12px; line-height: 1.45; }
.span-line .mono { color: var(--text); }
.span-line small { display: block; color: var(--faint); font-size: 11.5px; }
.zones .head > button.small { flex: none; margin-top: 4px; }
.settings .help strong { color: var(--text); font-weight: 600; }

/* === nouveau flux : endroits à ne jamais flouter === */
.ignore-tool { align-self: stretch; }
.ignore-note strong { font-weight: 600; }
.list.ignores li { align-items: center; padding: 6px 6px 6px 10px; font-size: 12.5px; }
.list.ignores li small { margin-top: 0; }
.list.ignores .sw-ignore { width: 18px; height: 14px; }
</style>
