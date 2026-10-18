//! 설정 파일(config.json). 화면이 고른 값을 그대로 담고, 서버는 SCRIBE_CONFIG로 같은 파일을 읽는다.
//!
//! 주요 키: data_dir, port, live, final, hf_token, open_browser, setup_done, packages_rev

use serde_json::{Map, Value};
use std::fs;
use std::path::Path;

pub type Config = Map<String, Value>;

pub const DEFAULT_PORT: u16 = 8765;

pub fn load(path: &Path) -> Config {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

pub fn save(path: &Path, cfg: &Config) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("설정 폴더를 만들지 못했어요: {e}"))?;
    }
    let tmp = path.with_extension("json.tmp");
    let text = serde_json::to_string_pretty(&Value::Object(cfg.clone())).map_err(|e| e.to_string())?;
    fs::write(&tmp, text).map_err(|e| format!("설정을 저장하지 못했어요: {e}"))?;
    fs::rename(&tmp, path).map_err(|e| format!("설정을 저장하지 못했어요: {e}"))?;
    #[cfg(unix)]
    {
        // Hugging Face 토큰이 들어 있으므로 나만 읽을 수 있게
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

pub fn get_str<'a>(cfg: &'a Config, key: &str) -> Option<&'a str> {
    cfg.get(key).and_then(|v| v.as_str()).filter(|s| !s.is_empty())
}

pub fn get_bool(cfg: &Config, key: &str, default: bool) -> bool {
    cfg.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}

pub fn port(cfg: &Config) -> u16 {
    cfg.get("port")
        .and_then(|v| v.as_u64())
        .and_then(|p| u16::try_from(p).ok())
        .filter(|p| *p >= 1024)
        .unwrap_or(DEFAULT_PORT)
}

/// 화면에서 보낸 값을 합친다 (null이면 지운다)
pub fn merge(cfg: &mut Config, patch: Config) {
    for (k, v) in patch {
        if v.is_null() {
            cfg.remove(&k);
        } else {
            cfg.insert(k, v);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn merge_and_port() {
        let mut cfg = Config::new();
        merge(&mut cfg, json!({"port": 9000, "hf_token": "x", "live": "a"}).as_object().unwrap().clone());
        merge(&mut cfg, json!({"hf_token": null, "final": "b"}).as_object().unwrap().clone());
        assert_eq!(port(&cfg), 9000);
        assert!(cfg.get("hf_token").is_none());
        assert_eq!(get_str(&cfg, "final"), Some("b"));
        assert_eq!(port(&Config::new()), DEFAULT_PORT);
        let mut bad = Config::new();
        bad.insert("port".into(), json!(80));
        assert_eq!(port(&bad), DEFAULT_PORT);
    }
}
