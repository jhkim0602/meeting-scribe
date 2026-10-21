//! 회의 노트 앱: 처음에는 설치 도우미, 그다음부터는 서버를 켜고 브라우저로 회의 노트를 연다.
//!
//! - 창(설치·설정): 처음 설치, 모델·저장 위치·화자 구분 바꾸기, 점검, 끄기
//! - 메뉴 막대 아이콘: 열기, 설치·설정 창, 폴더 열기, 서버 다시 켜기, 끝내기
//! - 창을 닫아도 서버는 계속 켜져 있다 (녹음이 끊기지 않게). 끝내기에서만 끈다.
//!
//! 창 없이 시험하기: meeting-scribe --headless install|serve|doctor --support DIR --resources DIR [--set 키=값]

#![cfg_attr(all(not(debug_assertions), windows), windows_subsystem = "windows")]

mod config;
mod install;
mod paths;
mod remove;
mod server;
mod sys;
mod update;
mod vcrt;

use paths::Paths;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

const READY_TIMEOUT: Duration = Duration::from_secs(120);

struct AppState {
    paths: Paths,
    config: Mutex<config::Config>,
    server: Arc<Mutex<server::Server>>,
    cancel: install::Cancel,
    installing: Mutex<bool>,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--headless") {
        std::process::exit(headless(&args[2..]));
    }
    run_app(args.iter().any(|a| a == "--background"));
}

// ---------------- 창, 메뉴 막대 ----------------

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn open_url(app: &AppHandle, url: &str) {
    let _ = app.opener().open_url(url, None::<&str>);
}

/// 서버를 켜고(이미 켜져 있으면 그대로) 준비되면 알린다. open이면 브라우저로 연다.
fn start_server(app: AppHandle, open: bool) {
    thread::spawn(move || {
        let state = app.state::<AppState>();
        let cfg = state.config.lock().unwrap().clone();
        let spawned = state.server.lock().unwrap().spawn(&state.paths, &cfg);
        let result = match spawned {
            Ok(true) => server::wait_ready(&state.server, &state.paths, READY_TIMEOUT),
            Ok(false) => Ok(()),
            Err(e) => {
                state.server.lock().unwrap().error = Some(e.clone());
                Err(e)
            }
        };
        let status = state.server.lock().unwrap().status();
        let _ = app.emit("server", &status);
        if result.is_ok() && open {
            let url = state.server.lock().unwrap().url();
            open_url(&app, &url);
        }
    });
}

/// 사용자가 끄지 않았는데 서버가 죽으면 다시 켠다 (5분 안에 3번까지). 녹음 중이었으면 화면이 다시 이어 붙인다.
fn watch_server(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(3));
        let state = app.state::<AppState>();
        let Some(reason) = state.server.lock().unwrap().crashed(&state.paths) else { continue };
        let log = state.paths.logs().join("server.log");
        let again = state.server.lock().unwrap().may_restart();
        let note = if again { "다시 켭니다" } else { "5분 안에 세 번 멈춰서 더는 다시 켜지 않습니다" };
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log) {
            use std::io::Write;
            let _ = writeln!(f, "[실행기] 서버가 멈췄어요 → {note}\n{reason}");
        }
        if again {
            start_server(app.clone(), false);
        } else {
            let status = {
                let mut s = state.server.lock().unwrap();
                s.wanted = false;
                s.error = Some(format!("서버가 5분 안에 세 번 멈춰서 더는 다시 켜지 않았어요. 메뉴의 '서버 다시 켜기'를 누르거나 로그를 확인해 주세요.\n{reason}"));
                s.status()
            };
            let _ = app.emit("server", &status);
        }
    });
}

fn stop_server(app: &AppHandle) {
    let state = app.state::<AppState>();
    state.server.lock().unwrap().stop();
}

/// 끝내기: 녹음 중이면 창에서 한 번 더 묻는다
fn request_quit(app: &AppHandle) {
    let state = app.state::<AppState>();
    let port = state.server.lock().unwrap().port;
    if port != 0 && server::recording(port) {
        show_main(app);
        let _ = app.emit("confirm-quit", ());
        return;
    }
    stop_server(app);
    app.exit(0);
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "회의 노트 열기", true, None::<&str>)?;
    let window = MenuItem::with_id(app, "window", "설치·설정 창", true, None::<&str>)?;
    let folder = MenuItem::with_id(app, "folder", "녹음·기록 폴더 열기", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "서버 다시 켜기", true, None::<&str>)?;
    let update = MenuItem::with_id(app, "update", "업데이트 확인", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "끝내기", true, None::<&str>)?;
    app.manage(update::UpdateMenu(update.clone()));
    let menu = Menu::with_items(
        app,
        &[&open, &window, &PredefinedMenuItem::separator(app)?, &folder, &restart, &update, &PredefinedMenuItem::separator(app)?, &quit],
    )?;
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    TrayIconBuilder::with_id("main")
        .icon(icon)
        .icon_as_template(true)
        .tooltip("회의 노트")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => {
                let state = app.state::<AppState>();
                if install::needs_install(&state.paths, &state.config.lock().unwrap()) {
                    show_main(app);
                } else {
                    start_server(app.clone(), true);
                }
            }
            "window" => show_main(app),
            "folder" => {
                let dir = data_dir(&app.state::<AppState>());
                let _ = std::fs::create_dir_all(&dir);
                let _ = app.opener().open_path(dir.display().to_string(), None::<&str>);
            }
            "restart" => {
                stop_server(app);
                start_server(app.clone(), false);
            }
            "update" => {
                // 창에서 확인하고, 있으면 '지금 업데이트' 버튼을 보여 준다
                show_main(app);
                let _ = app.emit("update-check-now", ());
            }
            "quit" => request_quit(app),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn data_dir(state: &AppState) -> PathBuf {
    config::get_str(&state.config.lock().unwrap(), "data_dir").map(PathBuf::from).unwrap_or_else(Paths::default_data_dir)
}

fn run_app(background: bool) {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--background"])))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(move |app| {
            let support = app.path().app_data_dir()?;
            let resources = std::env::var_os("MEETING_SCRIBE_RESOURCES").map(PathBuf::from).unwrap_or(app.path().resource_dir()?);
            let paths = Paths { support, resources };
            let cfg = config::load(&paths.config_file());
            let needs = install::needs_install(&paths, &cfg);
            let open = !background && config::get_bool(&cfg, "open_browser", true);
            app.manage(AppState {
                paths,
                config: Mutex::new(cfg),
                server: Arc::new(Mutex::new(server::Server::default())),
                cancel: install::Cancel::default(),
                installing: Mutex::new(false),
            });
            build_tray(app.handle())?;
            update::watch(app.handle().clone());
            watch_server(app.handle().clone());
            if needs {
                show_main(app.handle()); // 설치 도우미
            } else {
                if !background {
                    show_main(app.handle());
                }
                start_server(app.handle().clone(), open);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // 창을 닫아도 앱과 서버는 계속 (녹음이 끊기지 않게). 메뉴 막대의 '끝내기'로만 끈다.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_state,
            save_config,
            machine_info,
            free_space,
            pick_folder,
            install_start,
            install_cancel,
            is_recording,
            installer,
            server_start,
            server_stop,
            open_app,
            open_folder,
            open_link,
            autostart_set,
            quit,
            log_tail,
            update_check,
            update_install,
            storage_info,
            remove_models,
            clear_cache,
            uninstall_all,
            finish_uninstall,
        ])
        .build(tauri::generate_context!())
        .expect("회의 노트를 시작하지 못했어요")
        .run(|app, event| match event {
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => show_main(app),
            RunEvent::ExitRequested { code: None, api, .. } => {
                // ⌘Q 등: 녹음 중이면 멈추고 창에서 묻는다
                let state = app.state::<AppState>();
                let port = state.server.lock().unwrap().port;
                if port != 0 && server::recording(port) {
                    api.prevent_exit();
                    show_main(app);
                    let _ = app.emit("confirm-quit", ());
                }
            }
            RunEvent::Exit => stop_server(app),
            _ => {}
        });
}

// ---------------- 화면에서 부르는 명령 ----------------

#[tauri::command]
async fn update_check(app: AppHandle) -> Result<Option<Value>, String> {
    update::check(&app).await
}

/// 녹음 중이면 거절한다 (바꾸려면 서버를 꺼야 하므로)
#[tauri::command]
async fn update_install(app: AppHandle) -> Result<(), String> {
    let port = app.state::<AppState>().server.lock().unwrap().port;
    if port != 0 && server::recording(port) {
        return Err("녹음 중에는 업데이트할 수 없어요. 녹음을 마친 뒤 눌러 주세요.".into());
    }
    let (a, b) = (app.clone(), app.clone());
    update::install(&app, move || stop_server(&a), move || start_server(b.clone(), false)).await
}

#[tauri::command]
fn get_state(app: AppHandle, state: State<AppState>) -> Value {
    let cfg = reload_config(&state);
    json!({
        "config": cfg,
        "needs_install": install::needs_install(&state.paths, &cfg),
        "installing": *state.installing.lock().unwrap(),
        "paths": {
            "support": state.paths.support,
            "config": state.paths.config_file(),
            "logs": state.paths.logs(),
            "default_data_dir": Paths::default_data_dir(),
        },
        "server": state.server.lock().unwrap().status(),
        "version": app.package_info().version.to_string(),
        "autostart": app.autolaunch().is_enabled().unwrap_or(false),
        "misplaced": sys::misplaced(),
    })
}

/// 회의 화면(왼쪽 아래 모델 표시)에서 서버가 config.json의 실시간 모델을 바꿀 수 있다. 덮어쓰지 않게 파일에서 다시 읽는다.
fn reload_config(state: &AppState) -> config::Config {
    let fresh = config::load(&state.paths.config_file());
    let mut cfg = state.config.lock().unwrap();
    if !fresh.is_empty() {
        *cfg = fresh;
    }
    cfg.clone()
}

#[tauri::command]
fn save_config(state: State<AppState>, patch: config::Config) -> Result<config::Config, String> {
    reload_config(&state);
    let mut cfg = state.config.lock().unwrap();
    config::merge(&mut cfg, patch);
    config::save(&state.paths.config_file(), &cfg)?;
    Ok(cfg.clone())
}

#[tauri::command]
fn machine_info() -> Value {
    let mut m = sys::machine();
    m["free_gb"] = json!(sys::free_gb(&Paths::default_data_dir()));
    m
}

#[tauri::command]
fn free_space(path: String) -> Option<f64> {
    sys::free_gb(&PathBuf::from(path))
}

#[tauri::command]
async fn pick_folder(app: AppHandle, start: Option<String>) -> Option<String> {
    let mut dialog = app.dialog().file().set_title("녹음과 기록을 저장할 폴더");
    if let Some(s) = start.filter(|s| PathBuf::from(s).exists()) {
        dialog = dialog.set_directory(s);
    }
    tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_folder())
        .await
        .ok()
        .flatten()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

/// 녹음 중에 서버를 끄면 녹음이 끊긴다. 설정을 바꾸거나 서버를 다시 켜기 전에 확인한다.
const RECORDING_BUSY: &str = "녹음 중이라 지금은 바꿀 수 없어요. 녹음을 마친 뒤 다시 해 주세요.";

fn recording_now(state: &AppState) -> bool {
    let port = state.server.lock().unwrap().port;
    port != 0 && server::recording(port)
}

#[tauri::command]
async fn is_recording(state: State<'_, AppState>) -> Result<bool, String> {
    let port = state.server.lock().unwrap().port;
    tauri::async_runtime::spawn_blocking(move || port != 0 && server::recording(port)).await.map_err(|e| e.to_string())
}

#[tauri::command]
fn install_start(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    if recording_now(&state) {
        return Err(RECORDING_BUSY.into());
    }
    {
        let mut busy = state.installing.lock().unwrap();
        if *busy {
            return Ok(());
        }
        *busy = true;
    }
    // 설정이 바뀌었을 수 있으니 서버는 끄고 설치한 뒤 다시 켠다
    state.server.lock().unwrap().stop();
    let app2 = app.clone();
    thread::spawn(move || {
        let state = app2.state::<AppState>();
        let cfg = state.config.lock().unwrap().clone();
        let emit = |ev: install::Event| {
            let _ = app2.emit("install", &ev);
        };
        let result = install::run(&state.paths, &cfg, &state.cancel, &emit);
        if let Ok(updates) = &result {
            reload_config(&state);
            let mut cfg = state.config.lock().unwrap();
            config::merge(&mut cfg, updates.clone());
            let _ = config::save(&state.paths.config_file(), &cfg);
        }
        *state.installing.lock().unwrap() = false;
        match result {
            Ok(_) => emit(install::Event::new("all", "done", Some(1.0), "설치를 마쳤어요")),
            Err(_) => {} // 실패 알림은 install::run이 이미 보냈다
        }
    });
    Ok(())
}

#[tauri::command]
fn install_cancel(state: State<AppState>) {
    state.cancel.cancel();
}

/// python -m scribe.installer <args> (check-hf, claude, doctor, catalog)
#[tauri::command]
async fn installer(state: State<'_, AppState>, args: Vec<String>) -> Result<Value, String> {
    let paths = state.paths.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        install::run_installer(&paths, &refs)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn server_start(app: AppHandle) {
    start_server(app, false);
}

#[tauri::command]
fn server_stop(app: AppHandle) -> Result<Value, String> {
    let state = app.state::<AppState>();
    if recording_now(&state) {
        return Err(RECORDING_BUSY.into());
    }
    stop_server(&app);
    let status = state.server.lock().unwrap().status();
    Ok(status)
}

/// 회의 노트 열기: 서버가 꺼져 있으면 켜고 브라우저로 연다
#[tauri::command]
fn open_app(app: AppHandle) {
    start_server(app, true);
}

#[tauri::command]
fn open_folder(app: AppHandle, path: String) -> Result<(), String> {
    let _ = std::fs::create_dir_all(&path);
    app.opener().open_path(path, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_link(app: AppHandle, url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://127.0.0.1")) {
        return Err("열 수 없는 주소예요".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn autostart_set(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let manager = app.autolaunch();
    if enabled {
        manager.enable().map_err(|e| e.to_string())?;
    } else {
        manager.disable().map_err(|e| e.to_string())?;
    }
    manager.is_enabled().map_err(|e| e.to_string())
}

/// 끝내기. 녹음 중이면 force 없이는 끄지 않고 {"confirm": true}를 돌려준다.
#[tauri::command]
fn quit(app: AppHandle, force: bool) -> Value {
    let state = app.state::<AppState>();
    if !force && recording_now(&state) {
        return json!({"confirm": true});
    }
    stop_server(&app);
    app.exit(0);
    json!({"confirm": false})
}

// ---------------- 지우기 도우미 ----------------

/// 무엇이 얼마나 차지하는지: 받은 모델(서버 쪽에서 센다), 파이썬·패키지, 설치 캐시, 회의 기록, 앱
#[tauri::command]
async fn storage_info(state: State<'_, AppState>) -> Result<Value, String> {
    let paths = state.paths.clone();
    let data = data_dir(&state);
    tauri::async_runtime::spawn_blocking(move || {
        let mut v = remove::info(&paths, &data);
        v["models"] = if paths.python().exists() { install::run_installer(&paths, &["storage"]).unwrap_or(Value::Null) } else { Value::Null };
        v
    })
    .await
    .map_err(|e| e.to_string())
}

/// 고른 모델만 지운다 (다시 쓰면 그때 다시 받는다)
#[tauri::command]
async fn remove_models(state: State<'_, AppState>, repos: Vec<String>) -> Result<Value, String> {
    if recording_now(&state) {
        return Err("녹음 중이라 지금은 지울 수 없어요. 녹음을 마친 뒤 다시 해 주세요.".into());
    }
    let paths = state.paths.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["remove-models"];
        args.extend(repos.iter().map(String::as_str));
        install::run_installer(&paths, &args)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn clear_cache(state: State<'_, AppState>) -> Result<u64, String> {
    let paths = state.paths.clone();
    tauri::async_runtime::spawn_blocking(move || remove::clear_cache(&paths)).await.map_err(|e| e.to_string())
}

/// 완전히 지우기: Claude 연결 끊기 → (고르면) 모델 → 서버 끄기·로그인 항목 끄기 → (고르면) 회의 기록을 휴지통으로
/// → 앱 전용 폴더. 앱 자체는 finish_uninstall에서 (창이 결과를 보여 준 뒤)
#[tauri::command]
async fn uninstall_all(app: AppHandle, models: bool, meetings: bool) -> Result<Value, String> {
    let state = app.state::<AppState>();
    if recording_now(&state) {
        return Err("녹음 중이라 지금은 지울 수 없어요. 녹음을 마친 뒤 다시 해 주세요.".into());
    }
    let paths = state.paths.clone();
    let data = data_dir(&state);
    let app2 = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let _ = install::run_installer(&paths, &["claude", "disconnect"]);
        let models_freed = if models && paths.python().exists() {
            install::run_installer(&paths, &["remove-models", "--all"]).ok().and_then(|v| v["freed"].as_u64()).unwrap_or(0)
        } else {
            0
        };
        stop_server(&app2);
        let _ = app2.autolaunch().disable();
        let meetings_result = if meetings { remove::trash_meetings(&data) } else { Ok(false) };
        let support_freed = remove::remove_support(&paths);
        json!({
            "models_freed": models_freed,
            "support_freed": support_freed,
            "meetings_trashed": meetings_result.as_ref().map(|b| *b).unwrap_or(false),
            "meetings_error": meetings_result.err(),
            "data_dir": data,
            "app": remove::app_location().map(|p| p.display().to_string()),
        })
    })
    .await
    .map_err(|e| e.to_string())
}

/// 마지막: 앱을 지우고(맥: 휴지통, 윈도우: 제거 도구) 끝낸다
#[tauri::command]
fn finish_uninstall(app: AppHandle, remove_app: bool) -> Result<(), String> {
    if remove_app {
        if let Some(loc) = remove::app_location() {
            if cfg!(windows) {
                std::process::Command::new(loc.join("uninstall.exe")).spawn().map_err(|e| format!("제거 도구를 열지 못했어요: {e}"))?;
            } else {
                remove::to_trash(&loc).map_err(|e| format!("앱을 휴지통으로 옮기지 못했어요. 응용 프로그램 폴더에서 직접 지워 주세요.\n{e}"))?;
            }
        }
    }
    app.exit(0);
    Ok(())
}

#[tauri::command]
fn log_tail(state: State<AppState>, name: String, lines: Option<usize>) -> String {
    let file = if name == "install" { "install.log" } else { "server.log" };
    server::tail(&state.paths.logs().join(file), lines.unwrap_or(40))
}

// ---------------- 창 없이 (시험용) ----------------

fn headless(args: &[String]) -> i32 {
    let mut cmd = None;
    let mut support = None;
    let mut resources = None;
    let mut sets = config::Config::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--support" => support = it.next().map(PathBuf::from),
            "--resources" => resources = it.next().map(PathBuf::from),
            "--set" => {
                if let Some((k, v)) = it.next().and_then(|kv| kv.split_once('=')) {
                    let value = serde_json::from_str::<Value>(v).unwrap_or(Value::String(v.to_string()));
                    sets.insert(k.to_string(), value);
                }
            }
            other => cmd = Some(other.to_string()),
        }
    }
    let (Some(cmd), Some(support), Some(resources)) = (cmd, support, resources) else {
        eprintln!("사용법: meeting-scribe --headless install|serve|doctor --support DIR --resources DIR [--set 키=값]");
        return 2;
    };
    let paths = Paths { support, resources };
    let mut cfg = config::load(&paths.config_file());
    if !sets.is_empty() {
        config::merge(&mut cfg, sets);
        if let Err(e) = config::save(&paths.config_file(), &cfg) {
            eprintln!("{e}");
            return 1;
        }
    }
    let print = |ev: install::Event| println!("{}", serde_json::to_string(&ev).unwrap());
    match cmd.as_str() {
        "install" => match install::run(&paths, &cfg, &install::Cancel::default(), &print) {
            Ok(updates) => {
                config::merge(&mut cfg, updates);
                let _ = config::save(&paths.config_file(), &cfg);
                print(install::Event::new("all", "done", Some(1.0), "설치를 마쳤어요"));
                0
            }
            Err(_) => 1,
        },
        "serve" => {
            // 켜서 응답하는지 확인하고 끈다
            let server = Mutex::new(server::Server::default());
            let spawned = server.lock().unwrap().spawn(&paths, &cfg);
            let ok = match spawned {
                Ok(true) => server::wait_ready(&server, &paths, READY_TIMEOUT),
                Ok(false) => Ok(()),
                Err(e) => Err(e),
            };
            let status = server.lock().unwrap().status();
            println!("{}", json!({"ok": ok.is_ok(), "status": status, "error": ok.err()}));
            server.lock().unwrap().stop();
            if status["state"] == "running" { 0 } else { 1 }
        }
        "doctor" => match install::run_installer(&paths, &["doctor"]) {
            Ok(v) => {
                println!("{v}");
                0
            }
            Err(e) => {
                eprintln!("{e}");
                1
            }
        },
        _ => 2,
    }
}
