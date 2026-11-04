//! TaxBinder desktop shell (§11.4).
//!
//! Responsibilities:
//!  1. Start the frozen Python backend (bundled as a resource) and read its control channel.
//!  2. On `ready`, point the main window at the backend's one-time /bootstrap URL.
//!  3. On `open_portal`, open a separate window on a third-party portal and capture
//!     every download from it into `<vault>/inbox/portal/`, where the backend ingests it.
//!  4. "Save Page to TaxBinder" (menu, Cmd/Ctrl+Shift+S, or the main window's button):
//!     re-fetch the document shown in the last-used portal window and hand it over as
//!     a download, for portals that display PDFs inline instead of downloading them.
//!  5. Stop the backend when the app exits. (The backend also exits on its own when
//!     its stdin closes, so a crashed shell never leaves an orphan process.)

use std::{
    collections::HashMap,
    io::Read,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU32, Ordering},
        mpsc, Arc, Mutex,
    },
    time::Duration,
};

use percent_encoding::percent_decode_str;
use serde::Deserialize;
use tauri::{
    menu::{Menu, MenuItem, Submenu},
    webview::DownloadEvent,
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const CONTROL_PREFIX: &str = "@@TAXBINDER ";
/// The frozen backend: a folder bundled as a resource (the program and its libraries, each a
/// signed file in a release build).
const BACKEND: &str = "backend/taxbinder-backend";
const SAVE_PAGE_MENU_ID: &str = "save_page_to_taxbinder";
/// Larger text: the View menu zooms the main window, from 100% to 200%. Done here, by
/// the shell, so the backend's pages (remote content, no IPC) need no access to it.
const ZOOM_IN_ID: &str = "zoom_in";
const ZOOM_OUT_ID: &str = "zoom_out";
const ZOOM_RESET_ID: &str = "zoom_reset";
const ZOOMS: [f64; 6] = [1.0, 1.1, 1.25, 1.5, 1.75, 2.0];

/// Runs inside the portal page. Finds the document being shown (the page itself, or an
/// embedded PDF), re-fetches it with the page's own cookies, and triggers a blob download,
/// which the window's download handler then captures like any other download.
const CAPTURE_JS: &str = include_str!("capture.js");

/// Synchronous look at the page, so the log shows what the portal window is displaying.
const PROBE_JS: &str = r#"(function () {
  // Logged to stderr: no query strings (they can hold one-time tokens), no page title
  // (it can hold names or account numbers).
  try {
    var els = Array.prototype.slice.call(document.querySelectorAll("embed, object, iframe"));
    return JSON.stringify({
      href: (location.origin + location.pathname).slice(0, 200),
      contentType: document.contentType,
      embeds: els.slice(0, 5).map(function (e) {
        var src = (e.getAttribute("src") || e.getAttribute("data") || "").split(/[?#]/)[0];
        return (e.tagName + " " + (e.getAttribute("type") || "") + " " + src).slice(0, 160);
      })
    });
  } catch (err) { return "probe error: " + err; }
})()"#;

const MAX_NATIVE_DOWNLOAD: u64 = 100 * 1024 * 1024;

static PORTAL_SEQ: AtomicU32 = AtomicU32::new(0);

#[derive(Default)]
struct Backend {
    child: Mutex<Option<CommandChild>>,
    port: Mutex<Option<u16>>,
    /// Label of the portal window most recently focused; target of "Save Page".
    last_portal: Mutex<Option<String>>,
    portal_inbox: Mutex<Option<PathBuf>>,
    /// The main window's zoom (Make Text Bigger / Smaller).
    zoom: Mutex<f64>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum ControlMsg {
    Ready { port: u16, bootstrap: String },
    OpenPortal { url: String, label: Option<String> },
    CapturePage,
    Print,
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend::default())
        .setup(|app| {
            // TAXBINDER_VAULT points development runs at a throwaway vault; otherwise the real
            // vault in Application Support (com.4ampersand.taxbinder/vault).
            let vault = match std::env::var_os("TAXBINDER_VAULT") {
                Some(dir) => PathBuf::from(dir),
                None => app.path().app_data_dir()?.join("vault"),
            };
            std::fs::create_dir_all(vault.join("inbox").join("portal"))?;
            start_backend(app.handle(), &vault)?;

            // Standard menus (Edit menu matters: it enables Cmd+C/V in portal login forms)
            // plus our Document menu.
            let save = MenuItem::with_id(
                app,
                SAVE_PAGE_MENU_ID,
                "Save Page to TaxBinder",
                true,
                Some("CmdOrCtrl+Shift+S"),
            )?;
            let document = Submenu::with_items(app, "Document", true, &[&save])?;
            let menu = Menu::default(app.handle())?;
            let bigger = MenuItem::with_id(
                app,
                ZOOM_IN_ID,
                "Make Text Bigger",
                true,
                Some("CmdOrCtrl+Equal"),
            )?;
            let smaller = MenuItem::with_id(
                app,
                ZOOM_OUT_ID,
                "Make Text Smaller",
                true,
                Some("CmdOrCtrl+Minus"),
            )?;
            let actual =
                MenuItem::with_id(app, ZOOM_RESET_ID, "Actual Size", true, Some("CmdOrCtrl+0"))?;
            // Into the standard View menu (a new one if this platform's default has none).
            let view = menu.items()?.into_iter().find_map(|item| {
                item.as_submenu()
                    .filter(|s| s.text().ok().as_deref() == Some("View"))
                    .cloned()
            });
            match view {
                Some(view) => {
                    view.append_items(&[&bigger, &smaller, &actual])?;
                }
                None => {
                    let items: [&dyn tauri::menu::IsMenuItem<_>; 3] = [&bigger, &smaller, &actual];
                    menu.append(&Submenu::with_items(app, "View", true, &items)?)?;
                }
            }
            menu.append(&document)?;
            app.set_menu(menu)?;
            Ok(())
        })
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            if id == SAVE_PAGE_MENU_ID {
                capture_page(app);
            } else if let Some(step) = match id {
                ZOOM_IN_ID => Some(1),
                ZOOM_OUT_ID => Some(-1),
                ZOOM_RESET_ID => Some(0),
                _ => None,
            } {
                zoom(app, step);
            }
        })
        .on_window_event(|window, event| {
            let label = window.label();
            match event {
                // Closing the main window quits the app, even if portal windows are open.
                WindowEvent::Destroyed if label == "main" => window.app_handle().exit(0),
                WindowEvent::Focused(true) if label.starts_with("portal-") => {
                    *window.app_handle().state::<Backend>().last_portal.lock().unwrap() =
                        Some(label.to_string());
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build TaxBinder");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}

// ---------------------------------------------------------------------------
// Backend sidecar
// ---------------------------------------------------------------------------

fn start_backend(app: &AppHandle, vault: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let program = app.path().resource_dir()?.join(BACKEND);
    let (mut rx, child) = app
        .shell()
        .command(program)
        .args(["--vault", &vault.to_string_lossy()])
        .spawn()?;
    *app.state::<Backend>().child.lock().unwrap() = Some(child);
    *app.state::<Backend>().portal_inbox.lock().unwrap() = Some(vault.join("inbox").join("portal"));

    let handle = app.clone();
    let portal_inbox = vault.join("inbox").join("portal");
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    for line in String::from_utf8_lossy(&bytes).lines() {
                        let line = line.trim();
                        match line.strip_prefix(CONTROL_PREFIX) {
                            Some(json) => match serde_json::from_str::<ControlMsg>(json) {
                                Ok(msg) => handle_control(&handle, msg, &portal_inbox),
                                Err(e) => eprintln!("[shell] bad control message {json:?}: {e}"),
                            },
                            None if !line.is_empty() => eprintln!("[backend stdout] {line}"),
                            None => {}
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    eprintln!("[backend] {}", String::from_utf8_lossy(&bytes).trim_end())
                }
                CommandEvent::Error(e) => eprintln!("[shell] backend error: {e}"),
                CommandEvent::Terminated(p) => {
                    eprintln!("[shell] backend exited: code={:?} signal={:?}", p.code, p.signal);
                    show_backend_stopped(&handle, p.code);
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn stop_backend(app: &AppHandle) {
    if let Some(child) = app.state::<Backend>().child.lock().unwrap().take() {
        let _ = child.kill();
    }
}

fn handle_control(app: &AppHandle, msg: ControlMsg, portal_inbox: &Path) {
    match msg {
        ControlMsg::Ready { port, bootstrap } => {
            *app.state::<Backend>().port.lock().unwrap() = Some(port);
            let url = Url::parse(&format!("http://127.0.0.1:{port}/bootstrap?t={bootstrap}"))
                .expect("valid bootstrap URL");
            if let Some(main) = app.get_webview_window("main") {
                if let Err(e) = main.navigate(url) {
                    eprintln!("[shell] could not load backend UI: {e}");
                }
            }
        }
        ControlMsg::OpenPortal { url, label } => {
            let port = *app.state::<Backend>().port.lock().unwrap();
            match validate_portal_url(&url, port) {
                Ok(url) => {
                    if let Err(e) = open_portal(app, url, label, portal_inbox.to_path_buf()) {
                        eprintln!("[shell] could not open portal window: {e}");
                    }
                }
                Err(why) => eprintln!("[shell] refused portal URL ({} chars): {why}", url.len()),
            }
        }
        ControlMsg::CapturePage => capture_page(app),
        // The page's own window.print() does nothing in the app window; this opens the Mac's
        // print panel for it (Save as PDF is in the panel).
        ControlMsg::Print => match app.get_webview_window("main") {
            Some(main) => {
                if let Err(e) = main.print() {
                    eprintln!("[shell] could not open the print panel: {e}");
                }
            }
            None => eprintln!("[shell] print requested, but there is no main window"),
        },
    }
}

fn capture_page(app: &AppHandle) {
    let state = app.state::<Backend>();
    let label = state.last_portal.lock().unwrap().clone();
    let inbox = state.portal_inbox.lock().unwrap().clone();
    let window = label.as_deref().and_then(|l| app.get_webview_window(l));
    match (window, inbox) {
        (Some(w), Some(inbox)) => {
            eprintln!("[capture] save page requested for {}", w.label()); // no title: it can name the person
            // Off the main thread: we wait on script callbacks, and cookie reads can
            // deadlock on the main thread on Windows.
            std::thread::spawn(move || run_capture(w, inbox));
        }
        _ => eprintln!("[capture] save page requested, but no portal window is open"),
    }
}

/// Evaluate a script and wait for its (JSON-encoded) result.
fn eval_sync(w: &WebviewWindow, js: &str, timeout: Duration) -> Option<String> {
    let (tx, rx) = mpsc::channel();
    w.eval_with_callback(js, move |result| {
        let _ = tx.send(result);
    })
    .ok()?;
    rx.recv_timeout(timeout).ok()
}

/// Route 1: the in-page script (handles embedded viewers and blob: documents).
/// Route 2: fetch the window's current URL natively with the window's own cookies.
fn run_capture(w: WebviewWindow, inbox: PathBuf) {
    // An empty answer means page scripts don't run here: the macOS native PDF viewer
    // behaves this way (observed on Crestfall Capital). The in-page route can't work, so skip it.
    let probe = eval_sync(&w, PROBE_JS, Duration::from_secs(2)).filter(|p| !is_empty_result(p));
    match &probe {
        Some(_) => eprintln!("[capture] page scripts available"),
        None => eprintln!("[capture] page scripts not available (native PDF viewer?)"),
    }

    // TAXBINDER_CAPTURE_ROUTE=native skips the in-page route (testing the fallback).
    let force_native = std::env::var("TAXBINDER_CAPTURE_ROUTE").as_deref() == Ok("native");
    if force_native || probe.is_none() {
        if force_native {
            eprintln!("[capture] TAXBINDER_CAPTURE_ROUTE=native: skipping in-page route");
        }
    } else if let Err(e) = w.eval(CAPTURE_JS) {
        eprintln!("[capture] could not start in-page script: {e}");
    } else {
        let mut last = String::new();
        for _ in 0..20 {
            std::thread::sleep(Duration::from_millis(500));
            let Some(s) = eval_sync(&w, "JSON.stringify(window.__taxbinderCapture || null)", Duration::from_secs(2))
            else {
                continue;
            };
            if s != last {
                // the script's state only, never its details (file names, page text)
                let state = match (s.contains("TP_SAVED"), s.contains("TP_FAILED")) {
                    (true, _) => "saved",
                    (_, true) => "failed",
                    _ => "working",
                };
                eprintln!("[capture] script: {state}");
                last = s.clone();
            }
            if s.contains("TP_SAVED") {
                return; // the download handler takes it from here
            }
            if s.contains("TP_FAILED") {
                break;
            }
        }
    }

    eprintln!("[capture] trying native fetch with the window's session");
    match native_fetch(&w, &inbox) {
        Ok(_) => eprintln!("[capture] native fetch saved a file into the inbox"),
        Err(e) => eprintln!("[capture] native fetch failed: {e}"),
    }
}

fn native_fetch(w: &WebviewWindow, inbox: &Path) -> Result<PathBuf, String> {
    let url = w.url().map_err(|e| format!("could not read page URL: {e}"))?;
    if !matches!(url.scheme(), "https" | "http") {
        return Err(format!(
            "page URL is a {}: address, which only the page itself can read",
            url.scheme()
        ));
    }
    let cookies = w.cookies_for_url(url.clone()).map_err(|e| format!("could not read cookies: {e}"))?;
    // Cookie values are used for this one request only; never logged or stored.
    let cookie_header = cookies
        .iter()
        .map(|c| format!("{}={}", c.name(), c.value()))
        .collect::<Vec<_>>()
        .join("; ");
    let user_agent = eval_sync(w, "navigator.userAgent", Duration::from_secs(2))
        .and_then(|s| serde_json::from_str::<String>(&s).ok())
        .unwrap_or_else(|| "Mozilla/5.0".into());
    eprintln!("[capture] native GET from {} with {} cookie(s)", site(&url), cookies.len());

    let mut req = ureq::get(url.as_str()).header("User-Agent", &user_agent).header("Accept", "application/pdf,*/*");
    if !cookie_header.is_empty() {
        req = req.header("Cookie", &cookie_header);
    }
    let mut resp = req.call().map_err(|e| format!("request failed: {e}"))?;
    let content_type = resp
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_ascii_lowercase();
    let disposition = resp
        .headers()
        .get("content-disposition")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    let mut data = Vec::new();
    resp.body_mut()
        .as_reader()
        .take(MAX_NATIVE_DOWNLOAD)
        .read_to_end(&mut data)
        .map_err(|e| format!("reading body failed: {e}"))?;

    if !looks_like_document(&data) {
        return Err(format!(
            "portal returned {} ({} bytes), not a document; probably a login or viewer page",
            if content_type.is_empty() { "unknown content" } else { &content_type },
            data.len()
        ));
    }

    let title = w.title().unwrap_or_default();
    let name = sanitize_filename(&native_filename(disposition.as_deref(), &url, &title, &data));
    let final_path = unique_path(inbox, &name);
    let meta = serde_json::json!({
        "source_url": redact_query(&url),
        "suggested_name": name,
        "captured_via": "save_page_native",
    });
    std::fs::write(meta_path(&final_path), meta.to_string()).map_err(|e| e.to_string())?;
    let partial = partial_path(&final_path);
    std::fs::write(&partial, &data).map_err(|e| e.to_string())?;
    move_file(&partial, &final_path).map_err(|e| e.to_string())?;
    Ok(final_path)
}

fn is_empty_result(s: &str) -> bool {
    matches!(s.trim(), "" | "null" | "undefined" | "\"\"" | "{}")
}

fn looks_like_document(data: &[u8]) -> bool {
    data.starts_with(b"%PDF-")
        || data.starts_with(b"\x89PNG")
        || data.starts_with(&[0xFF, 0xD8, 0xFF])
        || data.starts_with(b"PK\x03\x04")
}

/// Content-Disposition filename, else last URL path segment, else the window title.
fn native_filename(disposition: Option<&str>, url: &Url, title: &str, data: &[u8]) -> String {
    let from_cd = disposition.and_then(|cd| {
        cd.split(';').map(str::trim).find_map(|part| {
            let (k, v) = part.split_once('=')?;
            let k = k.trim().to_ascii_lowercase();
            let v = v.trim().trim_matches('"');
            match k.as_str() {
                "filename*" => {
                    let v = v.rsplit("''").next().unwrap_or(v);
                    Some(percent_decode_str(v).decode_utf8_lossy().into_owned())
                }
                "filename" => Some(v.to_owned()),
                _ => None,
            }
        })
    });
    let from_url = || {
        url.path_segments()
            .and_then(|mut s| s.next_back().map(str::to_owned))
            .map(|s| percent_decode_str(&s).decode_utf8_lossy().into_owned())
            .filter(|s| Path::new(s).extension().is_some())
    };
    let mut name = from_cd
        .filter(|s| !s.is_empty())
        .or_else(from_url)
        .unwrap_or_else(|| if title.trim().is_empty() { "document".into() } else { title.trim().to_owned() });
    if Path::new(&name).extension().is_none() && data.starts_with(b"%PDF-") {
        name.push_str(".pdf");
    }
    name
}

/// Portal document URLs often carry one-time tokens in the query; keep them out of logs and records.
/// Where a request went, for logs: the site only (`https://host`), never the path, query or
/// file name (they can hold names, account numbers or one-time tokens).
/// The next zoom from `current`: one step bigger (1), smaller (-1), or back to 100% (0).
fn next_zoom(current: f64, step: i32) -> f64 {
    if step == 0 {
        return ZOOMS[0];
    }
    let at = ZOOMS
        .iter()
        .enumerate()
        .min_by(|a, b| (a.1 - current).abs().total_cmp(&(b.1 - current).abs()))
        .map_or(0, |(i, _)| i as i32);
    ZOOMS[(at + step).clamp(0, ZOOMS.len() as i32 - 1) as usize]
}

fn zoom(app: &AppHandle, step: i32) {
    let Some(main) = app.get_webview_window("main") else {
        return;
    };
    let state = app.state::<Backend>();
    let mut current = state.zoom.lock().unwrap();
    let next = next_zoom(if *current == 0.0 { 1.0 } else { *current }, step);
    match main.set_zoom(next) {
        Ok(()) => *current = next,
        Err(e) => eprintln!("[shell] could not zoom: {e}"),
    }
}

fn site(url: &Url) -> String {
    url.origin().ascii_serialization()
}

fn redact_query(url: &Url) -> String {
    let mut u = url.clone();
    if u.query().is_some() {
        u.set_query(Some("redacted"));
    }
    u.set_fragment(None);
    u.to_string()
}

fn show_backend_stopped(app: &AppHandle, code: Option<i32>) {
    // Only reached if the backend dies while the app is still running.
    if let Some(main) = app.get_webview_window("main") {
        let msg = format!(
            "document.body.innerHTML = '<div style=\"font:15px system-ui;padding:40px\">\
             <h2>TaxBinder stopped unexpectedly</h2><p>Backend exit code: {}</p>\
             <p>Please restart the app.</p></div>';",
            code.map(|c| c.to_string()).unwrap_or_else(|| "none".into())
        );
        let _ = main.eval(&msg);
    }
}

/// Defence in depth: the backend already validates, but the shell decides what it opens.
fn validate_portal_url(raw: &str, backend_port: Option<u16>) -> Result<Url, &'static str> {
    let url = Url::parse(raw).map_err(|_| "not a URL")?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err("embedded credentials");
    }
    match (url.scheme(), url.host_str()) {
        ("https", Some(_)) => Ok(url),
        ("http", Some("127.0.0.1")) if url.port().is_some() && url.port() == backend_port => Ok(url),
        _ => Err("only https portals (or the built-in test portal) are allowed"),
    }
}

// ---------------------------------------------------------------------------
// Portal windows with download capture
// ---------------------------------------------------------------------------

fn open_portal(
    app: &AppHandle,
    url: Url,
    label: Option<String>,
    inbox: PathBuf,
) -> tauri::Result<()> {
    let n = PORTAL_SEQ.fetch_add(1, Ordering::SeqCst) + 1;
    let title = label.unwrap_or_else(|| format!("Portal: {}", url.host_str().unwrap_or("")));
    // url string -> final path, so Finished can rename even if the platform
    // does not report the path it wrote to.
    let pending: Arc<Mutex<HashMap<String, PathBuf>>> = Arc::default();

    let window_label = format!("portal-{n}");
    WebviewWindowBuilder::new(app, &window_label, WebviewUrl::External(url))
        .title(title)
        .inner_size(1100.0, 820.0)
        .on_download(move |_webview, event| {
            match event {
                DownloadEvent::Requested { url, destination } => {
                    // Record what each platform proposes.
                    eprintln!("[portal] download requested from {}", site(&url)); // never the file name: it can name the person
                    let name = suggested_name(destination, &url);
                    let final_path = unique_path(&inbox, &name);
                    let meta = serde_json::json!({ "source_url": redact_query(&url), "suggested_name": name });
                    let _ = std::fs::write(meta_path(&final_path), meta.to_string());
                    *destination = partial_path(&final_path);
                    pending.lock().unwrap().insert(url.to_string(), final_path);
                    true // allow the download
                }
                DownloadEvent::Finished { url, path, success } => {
                    eprintln!("[portal] download finished from {} success={success}", site(&url));
                    let planned = pending.lock().unwrap().remove(url.as_str());
                    match (planned, path, success) {
                        (Some(final_path), path, true) => {
                            let written = path.unwrap_or_else(|| partial_path(&final_path));
                            if let Err(e) = move_file(&written, &final_path) {
                                eprintln!("[portal] could not move a download into the inbox: {}", e.kind());
                            }
                        }
                        (Some(final_path), path, false) => {
                            let _ = std::fs::remove_file(path.unwrap_or_else(|| partial_path(&final_path)));
                            let _ = std::fs::remove_file(meta_path(&final_path));
                        }
                        (None, Some(written), true) => {
                            // `Requested` never fired, so the webview chose the location itself
                            // (observed on Linux/WebKitGTK). Pull the finished file into the inbox.
                            let name = sanitize_filename(
                                &written.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
                            );
                            let final_path = unique_path(&inbox, &name);
                            let meta = serde_json::json!({
                                "source_url": redact_query(&url),
                                "suggested_name": name,
                                "captured_via": "finished_fallback",
                            });
                            let _ = std::fs::write(meta_path(&final_path), meta.to_string());
                            match move_file(&written, &final_path) {
                                Ok(()) => eprintln!("[portal] fallback: moved a download into the inbox"),
                                Err(e) => {
                                    eprintln!("[portal] fallback could not move a download: {}", e.kind());
                                    let _ = std::fs::remove_file(meta_path(&final_path));
                                }
                            }
                        }
                        (None, path, success) => {
                            eprintln!("[portal] untracked download: success={success} (file kept where the webview put it: {})", path.is_some())
                        }
                    }
                    true
                }
                _ => true,
            }
        })
        .build()?;
    *app.state::<Backend>().last_portal.lock().unwrap() = Some(window_label);
    Ok(())
}

/// Prefer the filename the webview proposes; fall back to the last URL path segment.
fn suggested_name(default_destination: &Path, url: &Url) -> String {
    let from_dest = default_destination
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .filter(|s| Path::new(s).extension().is_some());
    let from_url = || {
        url.path_segments()
            .and_then(|mut segs| segs.next_back().map(str::to_owned))
            .map(|s| percent_decode_str(&s).decode_utf8_lossy().into_owned())
            .filter(|s| Path::new(s).extension().is_some())
    };
    sanitize_filename(&from_dest.or_else(from_url).unwrap_or_else(|| "download.pdf".into()))
}

fn sanitize_filename(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect();
    let trimmed = cleaned.trim().trim_start_matches('.').trim_end_matches(['.', ' ']);
    let mut out: String = trimmed.chars().take(150).collect();
    if out.is_empty() {
        out = "download.pdf".into();
    }
    out
}

fn unique_path(dir: &Path, name: &str) -> PathBuf {
    let candidate = dir.join(name);
    if !candidate.exists() && !partial_path(&candidate).exists() {
        return candidate;
    }
    let p = Path::new(name);
    let stem = p.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let ext = p.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    (2..)
        .map(|i| dir.join(format!("{stem} ({i}){ext}")))
        .find(|c| !c.exists() && !partial_path(c).exists())
        .expect("unbounded search")
}

/// Rename, falling back to copy + delete when source and inbox are on different volumes.
fn move_file(from: &Path, to: &Path) -> std::io::Result<()> {
    match std::fs::rename(from, to) {
        Ok(()) => Ok(()),
        Err(_) => {
            std::fs::copy(from, to)?;
            std::fs::remove_file(from)
        }
    }
}

/// Hidden, suffixed name so the backend's inbox watcher ignores in-progress files.
fn partial_path(final_path: &Path) -> PathBuf {
    let name = final_path.file_name().unwrap_or_default().to_string_lossy();
    final_path.with_file_name(format!(".{name}.part"))
}

fn meta_path(final_path: &Path) -> PathBuf {
    let name = final_path.file_name().unwrap_or_default().to_string_lossy();
    final_path.with_file_name(format!("{name}.meta.json"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zoom_steps_from_100_to_200_percent() {
        assert_eq!(next_zoom(1.0, 1), 1.1);
        assert_eq!(next_zoom(1.5, -1), 1.25);
        assert_eq!(next_zoom(2.0, 1), 2.0); // no bigger than 200%
        assert_eq!(next_zoom(1.0, -1), 1.0); // no smaller than 100%
        assert_eq!(next_zoom(1.75, 0), 1.0); // Actual Size
        assert_eq!(next_zoom(1.3, 1), 1.5); // from the nearest step
    }

    #[test]
    fn control_messages_parse() {
        let print: ControlMsg = serde_json::from_str(r#"{"type":"print"}"#).unwrap();
        assert!(matches!(print, ControlMsg::Print));
        let capture: ControlMsg = serde_json::from_str(r#"{"type":"capture_page"}"#).unwrap();
        assert!(matches!(capture, ControlMsg::CapturePage));
    }

    #[test]
    fn logs_name_the_site_only() {
        let blob = Url::parse("blob:https://investors.example.com/d788ed90").unwrap();
        assert_eq!(site(&blob), "https://investors.example.com");
        let file = Url::parse("https://p.example/files/K-1%20Jane%20Doe.pdf?t=secret").unwrap();
        assert_eq!(site(&file), "https://p.example");
    }

    #[test]
    fn sanitizes_hostile_names() {
        assert_eq!(sanitize_filename("../../etc/passwd"), "_.._etc_passwd");
        assert_eq!(sanitize_filename("K-1 2025 (final) #1.pdf"), "K-1 2025 (final) #1.pdf");
        assert_eq!(sanitize_filename("a:b*c?.pdf"), "a_b_c_.pdf");
        assert_eq!(sanitize_filename("..."), "download.pdf");
        assert_eq!(sanitize_filename("report.pdf. "), "report.pdf");
    }

    #[test]
    fn suggests_from_destination_then_url() {
        let url = Url::parse("https://p.example/files/K-1%202025.pdf?x=1").unwrap();
        assert_eq!(suggested_name(Path::new("/Users/me/Downloads/stmt.pdf"), &url), "stmt.pdf");
        assert_eq!(suggested_name(Path::new("/Users/me/Downloads"), &url), "K-1 2025.pdf");
        let blob = Url::parse("blob:https://p.example/2f1c-44aa").unwrap();
        assert_eq!(suggested_name(Path::new(""), &blob), "download.pdf");
    }

    #[test]
    fn portal_url_rules() {
        assert!(validate_portal_url("https://portal.example.com/tax", Some(5000)).is_ok());
        assert!(validate_portal_url("http://127.0.0.1:5000/test-portal", Some(5000)).is_ok());
        assert!(validate_portal_url("http://127.0.0.1:5001/test-portal", Some(5000)).is_err());
        assert!(validate_portal_url("http://portal.example.com", Some(5000)).is_err());
        assert!(validate_portal_url("https://u:p@portal.example.com", Some(5000)).is_err());
        assert!(validate_portal_url("file:///etc/passwd", Some(5000)).is_err());
    }

    #[test]
    fn native_filename_sources() {
        let pdf = b"%PDF-1.4";
        let url = Url::parse("https://p.example/docs/view?id=9").unwrap();
        assert_eq!(native_filename(Some("inline; filename=\"1099 2025.pdf\""), &url, "t", pdf), "1099 2025.pdf");
        assert_eq!(
            native_filename(Some("attachment; filename*=UTF-8''K-1%20Fund%20III.pdf"), &url, "t", pdf),
            "K-1 Fund III.pdf"
        );
        let url2 = Url::parse("https://p.example/files/stmt-2025.pdf?sig=abc").unwrap();
        assert_eq!(native_filename(None, &url2, "t", pdf), "stmt-2025.pdf");
        assert_eq!(native_filename(None, &url, "ATLAS 1099", pdf), "ATLAS 1099.pdf");
    }

    #[test]
    fn document_detection_and_redaction() {
        assert!(looks_like_document(b"%PDF-1.7 ..."));
        assert!(!looks_like_document(b"<!DOCTYPE html><html>"));
        assert!(is_empty_result("") && is_empty_result("null") && is_empty_result(" {} "));
        assert!(!is_empty_result("\"{\\\"href\\\":1}\""));
        let u = Url::parse("https://p.example/a.pdf?token=secret#p=2").unwrap();
        assert_eq!(redact_query(&u), "https://p.example/a.pdf?redacted");
    }

    #[test]
    fn unique_and_partial_paths() {
        let dir = std::env::temp_dir().join(format!("tp-spike-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let first = unique_path(&dir, "a.pdf");
        assert_eq!(first, dir.join("a.pdf"));
        std::fs::write(&first, b"x").unwrap();
        assert_eq!(unique_path(&dir, "a.pdf"), dir.join("a (2).pdf"));
        assert_eq!(partial_path(&first), dir.join(".a.pdf.part"));
        assert_eq!(meta_path(&first), dir.join("a.pdf.meta.json"));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
