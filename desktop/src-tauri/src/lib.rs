//! Coque de bureau de Flouto.
//!
//! Elle ne contient aucune logique métier : elle démarre le moteur (le même programme que
//! `flouto ui`, embarqué dans l'application), attend qu'il annonce l'adresse locale de son
//! interface, puis l'affiche dans la fenêtre. À la fermeture, elle arrête le moteur.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{DragDropEvent, Manager, RunEvent, Url, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// Ligne imprimée par le moteur quand son serveur local est prêt (voir `server.py`).
const READY_PREFIX: &str = "FLOUTO_UI ";

struct Engine(Mutex<Option<Child>>);

/// Variable d'environnement `FLOUTO_<name>`, ou à défaut l'ancienne `MOTO_BLUR_<name>`.
fn env_var(name: &str) -> Option<String> {
    std::env::var(format!("FLOUTO_{name}"))
        .or_else(|_| std::env::var(format!("MOTO_BLUR_{name}")))
        .ok()
}

/// Commande de lancement du moteur.
///
/// 1. Version installée : l'exécutable embarqué dans les ressources de l'application.
/// 2. Développement : `FLOUTO_ENGINE` s'il est défini, sinon `uv run flouto` depuis le dépôt.
fn engine_command(app: &tauri::AppHandle) -> Command {
    let name = if cfg!(windows) { "flouto-engine.exe" } else { "flouto-engine" };
    if let Ok(resources) = app.path().resource_dir() {
        let bundled: PathBuf = resources.join("engine").join(name);
        if bundled.exists() {
            return Command::new(bundled);
        }
    }
    if let Some(custom) = env_var("ENGINE") {
        return Command::new(custom);
    }
    let mut command = Command::new("uv");
    command.args(["run", "--project", concat!(env!("CARGO_MANIFEST_DIR"), "/../.."), "flouto"]);
    command
}

fn start_engine(app: &tauri::AppHandle) -> Result<(Child, String), String> {
    let mut command = engine_command(app);
    command
        .args(["ui", "--no-browser", "--exit-with-parent"])
        .stdin(Stdio::piped()) // le moteur s'arrête quand ce tube se ferme (application quittée)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    if let Ok(home) = app.path().home_dir() {
        command.current_dir(home);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW : pas de console pour le moteur
    }
    let mut child = command.spawn().map_err(|e| format!("lancement impossible : {e}"))?;
    let stdout = child.stdout.take().ok_or("sortie du moteur indisponible")?;
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => return Err("le moteur s'est arrêté avant d'être prêt.".into()),
            Ok(_) => {
                if let Some(url) = line.trim().strip_prefix(READY_PREFIX) {
                    // On continue de vider la sortie du moteur pour qu'il ne se bloque jamais.
                    std::thread::spawn(move || {
                        let mut sink = String::new();
                        while matches!(reader.read_line(&mut sink), Ok(n) if n > 0) {
                            sink.clear();
                        }
                    });
                    return Ok((child, url.to_string()));
                }
            }
            Err(e) => return Err(format!("lecture de la sortie du moteur : {e}")),
        }
    }
}

fn show_error(window: &tauri::WebviewWindow, message: &str) {
    let text = format!("Le moteur n'a pas pu démarrer.\n{message}");
    let script = format!("window.motoBlurStatus && window.motoBlurStatus({text:?})");
    let _ = window.eval(&script);
}

/// Arrête le moteur (avant une mise à jour : l'installeur remplace ses fichiers).
fn stop_engine(handle: &tauri::AppHandle) {
    if let Some(mut child) = handle.state::<Engine>().0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Mise à jour : au démarrage seulement, l'application demande à la page des versions de
/// Flouto s'il en existe une plus récente. Si oui, elle le dit et propose de l'installer ;
/// rien n'est téléchargé sans accord. Le paquet est vérifié par sa signature avant d'être
/// installé. Hors ligne ou en cas d'erreur, rien ne s'affiche. Aucun autre appel réseau : le
/// traitement des vidéos reste entièrement local. `FLOUTO_NO_UPDATE=1` désactive la vérification.
fn check_update(handle: tauri::AppHandle) {
    if env_var("NO_UPDATE").is_some() {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let Ok(updater) = handle.updater() else { return };
        let Ok(Some(update)) = updater.check().await else { return };
        let text = format!(
            "La version {} de Flouto est disponible (vous avez la {}).\n\n\
             L'installer maintenant ? L'application se fermera puis redémarrera ; \
             vos projets sont conservés. Un traitement en cours serait interrompu.",
            update.version, update.current_version
        );
        let (tx, rx) = std::sync::mpsc::channel();
        handle
            .dialog()
            .message(text)
            .title("Mise à jour de Flouto")
            .buttons(MessageDialogButtons::OkCancelCustom("Mettre à jour".into(), "Plus tard".into()))
            .show(move |yes| {
                let _ = tx.send(yes);
            });
        let yes = tauri::async_runtime::spawn_blocking(move || rx.recv().unwrap_or(false))
            .await
            .unwrap_or(false);
        if !yes {
            return;
        }
        stop_engine(&handle);
        match update.download_and_install(|_, _| {}, || {}).await {
            Ok(()) => handle.restart(),
            Err(e) => {
                handle
                    .dialog()
                    .message(format!(
                        "La mise à jour n'a pas pu être installée ({e}).\n\n\
                         Fermez Flouto et relancez-le ; vous pouvez aussi télécharger la \
                         nouvelle version sur la page des versions."
                    ))
                    .title("Mise à jour de Flouto")
                    .kind(MessageDialogKind::Error)
                    .blocking_show();
                handle.exit(1);
            }
        }
    });
}

/// Fichiers déposés sur la fenêtre : leurs chemins sont remis à l'interface, qui ouvre la vidéo
/// ou le projet (`window.motoBlurDrop`, voir `ui/src/extras.ts`). Une page web seule ne connaît
/// pas le chemin d'un fichier déposé ; la coque, si.
fn drop_script(paths: &[PathBuf]) -> String {
    let list: Vec<String> = paths.iter().map(|p| format!("{:?}", p.to_string_lossy())).collect();
    format!("window.motoBlurDrop && window.motoBlurDrop([{}])", list.join(","))
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Engine(Mutex::new(None)))
        .on_window_event(|window, event| {
            if let WindowEvent::DragDrop(DragDropEvent::Drop { paths, .. }) = event {
                if let Some(webview) = window.app_handle().get_webview_window(window.label()) {
                    let _ = webview.eval(&drop_script(paths));
                }
            }
        })
        .setup(|app| {
            check_update(app.handle().clone());
            let handle = app.handle().clone();
            // Démarrage hors du fil principal : la fenêtre d'attente s'affiche tout de suite.
            std::thread::spawn(move || {
                let Some(window) = handle.get_webview_window("main") else { return };
                match start_engine(&handle) {
                    Ok((child, url)) => {
                        *handle.state::<Engine>().0.lock().unwrap() = Some(child);
                        match Url::parse(&url) {
                            Ok(parsed) => {
                                if let Err(e) = window.navigate(parsed) {
                                    show_error(&window, &e.to_string());
                                }
                            }
                            Err(e) => show_error(&window, &e.to_string()),
                        }
                    }
                    Err(message) => show_error(&window, &message),
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("erreur au démarrage de Flouto");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_engine(handle);
        }
    });
}
