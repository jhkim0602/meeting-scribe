//! 이 기기 정보 (설치 전이라 파이썬 없이): 칩, 메모리, 남은 공간

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

fn sh(cmd: &str, args: &[&str]) -> String {
    Command::new(cmd)
        .args(args)
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

pub fn ram_gb() -> Option<f64> {
    let mem = sh("sysctl", &["-n", "hw.memsize"]);
    if let Ok(bytes) = mem.parse::<f64>() {
        return Some((bytes / 1024f64.powi(3) * 10.0).round() / 10.0);
    }
    let info = std::fs::read_to_string("/proc/meminfo").ok()?;
    let kb: f64 = info.lines().find(|l| l.starts_with("MemTotal:"))?.split_whitespace().nth(1)?.parse().ok()?;
    Some((kb / 1024f64.powi(2) * 10.0).round() / 10.0)
}

fn chip() -> String {
    let c = sh("sysctl", &["-n", "machdep.cpu.brand_string"]);
    if !c.is_empty() {
        return c;
    }
    std::fs::read_to_string("/proc/cpuinfo")
        .ok()
        .and_then(|s| s.lines().find(|l| l.starts_with("model name")).and_then(|l| l.split(':').nth(1)).map(|s| s.trim().to_string()))
        .unwrap_or_else(|| std::env::consts::ARCH.to_string())
}

fn os_name() -> String {
    if cfg!(target_os = "macos") {
        format!("macOS {}", sh("sw_vers", &["-productVersion"]))
    } else {
        std::env::consts::OS.to_string()
    }
}

/// 이 경로(없으면 가장 가까운 상위 폴더)가 있는 디스크의 남은 공간 (GB)
pub fn free_gb(path: &Path) -> Option<f64> {
    let mut p = PathBuf::from(path);
    while !p.exists() {
        if !p.pop() {
            return None;
        }
    }
    let out = sh("df", &["-Pk", &p.display().to_string()]);
    let kb: f64 = out.lines().nth(1)?.split_whitespace().nth(3)?.parse().ok()?;
    Some((kb / 1024f64.powi(2) * 10.0).round() / 10.0)
}

pub fn machine() -> Value {
    json!({
        "chip": chip(),
        "ram_gb": ram_gb(),
        "os": os_name(),
        "apple_silicon": cfg!(target_os = "macos") && std::env::consts::ARCH == "aarch64",
        "arch": std::env::consts::ARCH,
        "platform": std::env::consts::OS,
    })
}

/// 디스크 이미지나 '다운로드' 폴더에서 바로 연 맥 앱은 열 때마다 바뀌는 임시 위치(App Translocation)에서 돈다.
/// 그러면 로그인할 때 켜기가 엉뚱한 곳을 가리키므로 응용 프로그램 폴더로 옮기라고 알린다.
pub fn misplaced() -> bool {
    cfg!(target_os = "macos") && std::env::current_exe().map(|p| misplaced_path(&p.to_string_lossy())).unwrap_or(false)
}

fn misplaced_path(exe: &str) -> bool {
    exe.contains("/AppTranslocation/") || exe.starts_with("/Volumes/")
}

#[cfg(test)]
mod tests {
    #[test]
    fn translocated_or_dmg_is_misplaced() {
        assert!(super::misplaced_path("/private/var/folders/x/T/AppTranslocation/ABC/d/회의 노트.app/Contents/MacOS/meeting-scribe"));
        assert!(super::misplaced_path("/Volumes/회의 노트/회의 노트.app/Contents/MacOS/meeting-scribe"));
        assert!(!super::misplaced_path("/Applications/회의 노트.app/Contents/MacOS/meeting-scribe"));
    }

    #[test]
    fn free_space_of_missing_folder_uses_parent() {
        let p = std::env::temp_dir().join("없는-폴더").join("더-깊이");
        assert!(super::free_gb(&p).unwrap_or(0.0) > 0.0);
        assert!(super::ram_gb().unwrap_or(0.0) > 0.0);
    }
}
