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

/// 회의 기록 폴더: 이 앱이 만든 폴더(scribe.db가 있는)만 휴지통으로
pub fn trash_meetings(data_dir: &Path) -> Result<bool, String> {
    if !data_dir.join("scribe.db").exists() {
        return Ok(false);
    }
    to_trash(data_dir).map(|_| true)
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
        assert_eq!(remove_support(&paths), 302);
        assert!(!paths.support.exists());
        let _ = fs::remove_dir_all(dir);
    }
}
