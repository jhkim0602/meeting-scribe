//! 지우기 도우미: 받은 모델·앱 전용 파이썬·설치 캐시가 얼마나 차지하는지 보여 주고 지운다.
//!
//! - 모델은 서버 쪽(`scribe.installer remove-models`)이 지운다. Hugging Face 캐시는 다른 프로그램과 같이 쓰므로
//!   이 앱이 받은 모델만, 같이 쓰는 큰 파일은 남기고 지운다.
//! - 회의 기록은 기본으로 남긴다. 지우기로 고르면 휴지통으로 옮긴다 (되살릴 수 있게).
//! - 앱: 맥은 앱 묶음을 휴지통으로, 윈도우는 설치 프로그램의 제거 도구(uninstall.exe)를 띄운다.

use crate::paths::Paths;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

/// 폴더 크기 (링크는 따라가지 않는다)
pub fn dir_size(path: &Path) -> u64 {
    let Ok(meta) = fs::symlink_metadata(path) else { return 0 };
    if !meta.is_dir() {
        return if meta.file_type().is_symlink() { 0 } else { meta.len() };
    }
    fs::read_dir(path).map(|it| it.flatten().map(|e| dir_size(&e.path())).sum()).unwrap_or(0)
}

/// 이 앱 묶음(맥 .app) 또는 설치 폴더(윈도우)
pub fn app_location() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    if cfg!(target_os = "macos") {
        // …/회의 노트.app/Contents/MacOS/meeting-scribe
        let bundle = exe.parent()?.parent()?.parent()?.to_path_buf();
        return bundle.extension().map(|e| e == "app").unwrap_or(false).then_some(bundle);
    }
    if cfg!(windows) {
        let dir = exe.parent()?.to_path_buf();
        return dir.join("uninstall.exe").exists().then_some(dir);
    }
    None
}

/// 앱 전용 폴더에서 무엇이 얼마나 차지하는지
pub fn info(paths: &Paths, data_dir: &Path) -> Value {
    let packages = dir_size(&paths.venv()) + dir_size(&paths.support.join("python"));
    let cache = dir_size(&paths.support.join("cache"));
    let support = dir_size(&paths.support);
    let app = app_location();
    json!({
        "packages": packages,
        "cache": cache,
        "other": support.saturating_sub(packages + cache),
        "support": paths.support,
        "data_dir": data_dir,
        "data": dir_size(data_dir),
        "app": app.as_ref().map(|p| p.display().to_string()),
        "app_bytes": app.as_ref().map(|p| dir_size(p)).unwrap_or(0),
    })
}

/// 설치 캐시 비우기 (패키지를 받을 때만 쓰고, 다시 설치하면 새로 받는다)
pub fn clear_cache(paths: &Paths) -> u64 {
    let dir = paths.support.join("cache");
    let before = dir_size(&dir);
    let _ = fs::remove_dir_all(&dir);
    before.saturating_sub(dir_size(&dir))
}

/// 휴지통으로 옮긴다 (맥은 Finder를 부르지 않는 방식으로: 권한 창이 뜨지 않게)
pub fn to_trash(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        use trash::macos::{DeleteMethod, TrashContextExtMacos};
        let mut ctx = trash::TrashContext::default();
        ctx.set_delete_method(DeleteMethod::NsFileManager);
        ctx.delete(path).map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "macos"))]
    {
        trash::delete(path).map_err(|e| e.to_string())
    }
}

/// 서버가 저장 폴더에 만드는 것: DB(scribe.db, -wal, -shm, 깨져서 옮겨 둔 사본), 회의 폴더, 하루 한 번 사본, 백업용 임시 사본
fn ours(name: &str) -> bool {
    name.starts_with("scribe.db") || matches!(name, "meetings" | ".backups" | ".backup-snapshot.db")
}

/// 운영체제가 저절로 만드는 파일 (있어도 '다른 파일'로 치지 않는다)
fn os_junk(name: &str) -> bool {
    matches!(name, ".DS_Store" | "Thumbs.db" | "desktop.ini")
}

/// 회의 기록을 휴지통으로: 이 앱이 만든 것만 옮긴다.
/// 저장 위치를 '문서'처럼 다른 파일이 있는 폴더로 골랐어도 그 파일들은 건드리지 않는다.
/// 폴더에 이 앱 것만 있으면 폴더째 옮긴다. 옮긴 것이 있으면 true.
pub fn trash_meetings(data_dir: &Path) -> Result<bool, String> {
    if !data_dir.join("scribe.db").exists() {
        return Ok(false); // 이 앱의 저장 폴더가 아니다
    }
    let names: Vec<String> = fs::read_dir(data_dir)
        .map_err(|e| e.to_string())?
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    if names.iter().all(|n| ours(n) || os_junk(n)) {
        return to_trash(data_dir).map(|_| true);
    }
    for name in names.iter().filter(|n| ours(n)) {
        to_trash(&data_dir.join(name))?;
    }
    Ok(true)
}

/// 앱 전용 폴더(파이썬, 패키지, 설정, 로그, 캐시)를 지운다. 지운 양
pub fn remove_support(paths: &Paths) -> u64 {
    let before = dir_size(&paths.support);
    let _ = fs::remove_dir_all(&paths.support);
    before.saturating_sub(dir_size(&paths.support)) // 윈도우에서 창이 쓰는 파일은 남을 수 있다 (제거 도구가 지운다)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 실제로 휴지통에 옮겨 본다 (리눅스: XDG 휴지통을 임시 폴더로)
    #[cfg(target_os = "linux")]
    #[test]
    fn mixed_folder_keeps_users_files() {
        let dir = std::env::temp_dir().join(format!("ms-trash-{}", std::process::id()));
        std::env::set_var("XDG_DATA_HOME", dir.join("xdg"));
        let docs = dir.join("문서");
        fs::create_dir_all(docs.join("meetings").join("회의1")).unwrap();
        fs::create_dir_all(docs.join(".backups")).unwrap();
        fs::write(docs.join("scribe.db"), "db").unwrap();
        fs::write(docs.join("scribe.db-wal"), "wal").unwrap();
        fs::write(docs.join("보고서.docx"), "내 파일").unwrap();
        assert_eq!(trash_meetings(&docs), Ok(true));
        let left: Vec<_> = fs::read_dir(&docs).unwrap().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        assert_eq!(left, vec!["보고서.docx".to_string()]);
        // 앱 것만 있는 폴더는 폴더째
        let only = dir.join("MeetingScribe");
        fs::create_dir_all(only.join("meetings")).unwrap();
        fs::write(only.join("scribe.db"), "db").unwrap();
        assert_eq!(trash_meetings(&only), Ok(true));
        assert!(!only.exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn sizes_cache_and_meetings_guard() {
        let dir = std::env::temp_dir().join(format!("ms-rm-{}", std::process::id()));
        let paths = Paths { support: dir.join("s"), resources: dir.join("r") };
        fs::create_dir_all(paths.venv().join("lib")).unwrap();
        fs::write(paths.venv().join("lib").join("a"), vec![0u8; 300]).unwrap();
        fs::create_dir_all(paths.support.join("cache").join("uv")).unwrap();
        fs::write(paths.support.join("cache").join("uv").join("b"), vec![0u8; 200]).unwrap();
        fs::write(paths.support.join("config.json"), "{}").unwrap();
        let data = dir.join("회의 기록");
        fs::create_dir_all(&data).unwrap();
        let v = info(&paths, &data);
        assert_eq!(v["packages"], 300);
        assert_eq!(v["cache"], 200);
        assert_eq!(v["other"], 2);
        assert_eq!(clear_cache(&paths), 200);
        assert!(!paths.support.join("cache").exists());
        // scribe.db가 없는 폴더는 회의 기록 폴더로 보지 않는다 (다른 파일을 지우지 않게)
        assert_eq!(trash_meetings(&data), Ok(false));
        assert!(data.exists());
        // 앱 것과 사용자 파일이 섞인 폴더(예: '문서')에서는 앱 것만 고른다
        for n in ["scribe.db", "scribe.db-wal", "meetings", ".backups", ".backup-snapshot.db"] {
            assert!(ours(n), "{n}");
        }
        for n in ["보고서.docx", "meetings-old", "my-scribe.db", "사진"] {
            assert!(!ours(n) && !os_junk(n), "{n}");
        }
        assert_eq!(remove_support(&paths), 302);
        assert!(!paths.support.exists());
        let _ = fs::remove_dir_all(dir);
    }
}
