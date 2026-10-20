//! 자동 업데이트: GitHub 릴리스의 latest.json을 보고 새 버전이 있으면 창과 메뉴 막대에 알린다.
//! 누르면 받고(진행률을 창에 보낸다), 서버를 끄고, 바꾸고, 다시 켠다. 녹음 중에는 바꾸지 않는다.
//! 받은 파일은 릴리스에 함께 올린 서명(.sig)으로 확인한다 (tauri.conf.json의 pubkey).

use serde_json::{json, Value};
use std::thread;
use std::time::Duration;
use tauri::menu::MenuItem;
use tauri::{AppHandle, Emitter, Manager, Wry};
use tauri_plugin_updater::UpdaterExt;

/// 메뉴 막대의 '업데이트 확인' 항목 (새 버전이 있으면 글자를 바꾼다)
pub struct UpdateMenu(pub MenuItem<Wry>);

/// 새 버전이 있으면 {version, current, notes}
pub async fn check(app: &AppHandle) -> Result<Option<Value>, String> {
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    let found = update.map(|u| json!({ "version": u.version, "current": u.current_version, "notes": u.body }));
    if let (Some(v), Some(menu)) = (&found, app.try_state::<UpdateMenu>()) {
        let _ = menu.0.set_text(format!("업데이트 설치 (v{})", v["version"].as_str().unwrap_or("")));
    }
    Ok(found)
}

/// 받기 → (받은 뒤) 서버 끄기 → 바꾸기 → 다시 켜기. 실패하면 서버를 다시 켜고 이유를 돌려준다.
pub async fn install(app: &AppHandle, stop_server: impl Fn(), start_server: impl Fn()) -> Result<(), String> {
    let update = app
        .updater()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| format!("업데이트를 확인하지 못했어요: {e}"))?
        .ok_or("이미 최신 버전이에요")?;
    let mut done: u64 = 0;
    let bytes = update
        .download(
            |chunk, total| {
                done += chunk as u64;
                let _ = app.emit("update-progress", json!({ "done": done, "total": total }));
            },
            || {},
        )
        .await
        .map_err(|e| format!("새 버전을 받지 못했어요. 인터넷 연결을 확인해 주세요.\n{e}"))?;
    stop_server();
    if let Err(e) = update.install(bytes) {
        start_server();
        return Err(format!("새 버전으로 바꾸지 못했어요.\n{e}"));
    }
    app.restart();
}

/// 켤 때 한 번, 그 뒤 6시간마다 확인한다 (인터넷이 없으면 조용히 넘어간다)
pub fn watch(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(20)); // 켜자마자 서버·설치와 겹치지 않게
        if let Ok(Some(v)) = tauri::async_runtime::block_on(check(&app)) {
            let _ = app.emit("update-available", v);
        }
        thread::sleep(Duration::from_secs(6 * 60 * 60));
    });
}
