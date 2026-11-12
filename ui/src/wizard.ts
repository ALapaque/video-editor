// Position de l'assistant du mode simple. Elle vit hors du composant pour survivre à un passage
// par le mode avancé ; à l'ouverture d'un projet, elle est de toute façon déduite de son état.

import { reactive } from "vue";

export type Screen = "video" | "what" | "object" | "trim" | "analyse" | "verify" | "export" | "done";

export const wiz = reactive({
  screen: "video" as Screen,
  path: "", // projet auquel la position se rapporte
  objectWanted: false, // « Un objet précis » coché, avant même d'en avoir désigné un
  launched: false, // analyse lancée depuis l'assistant pendant cette séance
  launchedExport: false,
  exportFor: "", // projet pour lequel le nom du fichier de sortie a été proposé
  output: "", // chemin de la dernière vidéo créée
});
