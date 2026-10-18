//! 처음 설치: 파이썬 → 패키지 → 모델 → 점검. 단계마다 진행 상황을 알린다.
//!
//! 앱에 들어 있는 uv로 앱 전용 폴더에 파이썬과 패키지를 설치하므로 Homebrew나 시스템 파이썬이 필요 없다.
//! 모델 받기·점검은 설치한 파이썬으로 `python -m scribe.installer`를 불러 JSON 줄을 읽는다.

use crate::config::{self, Config};
use crate::paths::Paths;
use serde::Serialize;
use serde_json::Value;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;

pub const PYTHON_VERSION: &str = "3.12";

#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Event {
    /// python | packages | models | check | all
    pub step: String,
    /// running | done | skip | warn | error
    pub status: String,
    /// 0~1 (모르면 없음)
    pub progress: Option<f64>,
    pub message: String,
    pub detail: Option<Value>,
}

impl Event {
    pub fn new(step: &str, status: &str, progress: Option<f64>, message: impl Into<String>) -> Self {
        Event { step: step.into(), status: status.into(), progress, message: message.into(), detail: None }
    }
    pub fn with(mut self, detail: Value) -> Self {
        self.detail = Some(detail);
        self
    }
}

/// 하던 설치를 멈추기 위해 지금 돌고 있는 프로세스를 기억한다
#[derive(Clone, Default)]
pub struct Cancel {
    pid: Arc<Mutex<Option<u32>>>,
    cancelled: Arc<Mutex<bool>>,
}

impl Cancel {
    pub fn cancel(&self) {
        *self.cancelled.lock().unwrap() = true;
        if let Some(pid) = *self.pid.lock().unwrap() {
            crate::server::kill_tree(pid, false);
        }
    }
    pub fn is_cancelled(&self) -> bool {
        *self.cancelled.lock().unwrap()
    }
    pub fn reset(&self) {
        *self.cancelled.lock().unwrap() = false;
    }
}

/// 서버·설치 명령에 공통으로 주는 환경 변수
pub fn python_env(paths: &Paths) -> Vec<(String, String)> {
    let mut path = String::from("/usr/bin:/bin:/usr/sbin:/sbin");
    if let Ok(p) = std::env::var("PATH") {
        path = format!("{path}:{p}");
    }
    vec![
        ("PYTHONPATH".into(), paths.backend().display().to_string()),
        ("SCRIBE_CONFIG".into(), paths.config_file().display().to_string()),
        ("SCRIBE_FRONTEND_DIST".into(), paths.frontend().display().to_string()),
        // 앱 안(서명된 번들)에 .pyc를 쓰지 않는다
        ("PYTHONPYCACHEPREFIX".into(), paths.support.join("pycache").display().to_string()),
        ("PYTHONUNBUFFERED".into(), "1".into()),
        ("HF_HUB_DISABLE_TELEMETRY".into(), "1".into()),
        ("PATH".into(), if cfg!(windows) { std::env::var("PATH").unwrap_or_default() } else { path }),
    ]
}

fn uv_env(paths: &Paths) -> Vec<(String, String)> {
    vec![
        ("UV_PYTHON_INSTALL_DIR".into(), paths.support.join("python").display().to_string()),
        ("UV_CACHE_DIR".into(), paths.support.join("cache").join("uv").display().to_string()),
        ("UV_NO_PROGRESS".into(), "1".into()),
    ]
}

/// 앱에 든 uv를 앱 전용 폴더에 새로 써서 그쪽을 쓴다.
/// 내려받은 앱 안의 파일에는 맥의 격리 표시가 붙어 있어 서명 없는 앱에서는 바로 실행되지 않을 수 있다.
/// 이 앱이 직접 쓴 파일에는 그런 표시가 붙지 않는다.
fn local_uv(paths: &Paths) -> Result<PathBuf, String> {
    let bundled = paths.uv();
    let bytes = fs::read(&bundled).map_err(|_| format!("앱에 설치 도구(uv)가 없어요: {}", bundled.display()))?;
    let dir = paths.support.join("bin");
    let local = dir.join(bundled.file_name().unwrap_or_default());
    if fs::read(&local).map(|b| b == bytes).unwrap_or(false) {
        return Ok(local);
    }
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let tmp = dir.join(".uv.tmp");
    fs::write(&tmp, &bytes).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&tmp, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    }
    fs::rename(&tmp, &local).map_err(|e| e.to_string())?;
    Ok(local)
}

/// 패키지 목록이 바뀌었는지 알기 위한 지문 (FNV-1a)
pub fn packages_rev(paths: &Paths) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for f in [paths.backend().join("requirements.txt"), paths.requirements()] {
        for b in fs::read(&f).unwrap_or_default() {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3);
        }
    }
    format!("{h:016x}")
}

/// 설치가 필요한지: 처음이거나, 앱이 바뀌어 패키지 목록이 달라졌거나, 파이썬이 없어졌거나
pub fn needs_install(paths: &Paths, cfg: &Config) -> bool {
    !config::get_bool(cfg, "setup_done", false)
        || config::get_str(cfg, "packages_rev") != Some(packages_rev(paths).as_str())
        || !paths.python().exists()
}

/// 명령을 실행하며 출력 줄(표준 출력·오류 모두)을 차례로 넘긴다. 로그 파일에도 남긴다.
pub fn run_lines(
    mut cmd: Command,
    cancel: &Cancel,
    log: &std::path::Path,
    mut on_line: impl FnMut(&str),
) -> Result<(), String> {
    let mut logf = OpenOptions::new().create(true).append(true).open(log).ok();
    if let Some(f) = logf.as_mut() {
        let _ = writeln!(f, "$ {:?}", cmd);
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).stdin(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // 멈출 때 자식 프로세스까지 함께
    }
    let mut child = cmd.spawn().map_err(|e| format!("실행하지 못했어요: {e}"))?;
    *cancel.pid.lock().unwrap() = Some(child.id());
    let (tx, rx) = mpsc::channel::<String>();
    let out = child.stdout.take().unwrap();
    let err = child.stderr.take().unwrap();
    let tx2 = tx.clone();
    let t1 = thread::spawn(move || {
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            let _ = tx.send(line);
        }
    });
    let t2 = thread::spawn(move || {
        for line in BufReader::new(err).lines().map_while(Result::ok) {
            let _ = tx2.send(line);
        }
    });
    let mut tail: Vec<String> = Vec::new();
    for line in rx {
        if let Some(f) = logf.as_mut() {
            let _ = writeln!(f, "{line}");
        }
        tail.push(line.clone());
        if tail.len() > 12 {
            tail.remove(0);
        }
        on_line(&line);
    }
    let _ = t1.join();
    let _ = t2.join();
    let status = child.wait().map_err(|e| e.to_string())?;
    *cancel.pid.lock().unwrap() = None;
    if cancel.is_cancelled() {
        return Err("설치를 멈췄어요".into());
    }
    if status.success() {
        Ok(())
    } else {
        Err(tail.join("\n"))
    }
}

/// uv pip install 출력으로 진행률 어림: 풀기 10% → 받기(받은 개수) → 설치 100%
#[derive(Default, Debug)]
pub struct UvProgress {
    downloading: usize,
    downloaded: usize,
    pub progress: f64,
    pub message: String,
}

impl UvProgress {
    pub fn feed(&mut self, line: &str) -> bool {
        let l = line.trim();
        let before = self.progress;
        if l.starts_with("Resolved ") {
            self.progress = self.progress.max(0.1);
            self.message = "필요한 패키지를 확인했어요".into();
        } else if let Some(rest) = l.strip_prefix("Downloading ") {
            self.downloading += 1;
            self.message = format!("받는 중: {}", rest);
        } else if l.starts_with("Downloaded ") {
            self.downloaded += 1;
            let frac = self.downloaded as f64 / self.downloading.max(self.downloaded).max(1) as f64;
            self.progress = self.progress.max(0.1 + 0.75 * frac);
        } else if l.starts_with("Prepared ") {
            self.progress = self.progress.max(0.9);
            self.message = "설치하는 중".into();
        } else if l.starts_with("Installed ") || l.starts_with("Audited ") {
            self.progress = 1.0;
            self.message = "패키지 설치를 마쳤어요".into();
        } else {
            return false;
        }
        self.progress > before || !self.message.is_empty()
    }
}

fn python_works(paths: &Paths) -> bool {
    paths.python().exists()
        && Command::new(paths.python())
            .args(["-c", "import sys; assert sys.version_info >= (3, 11)"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
}

fn installer_cmd(paths: &Paths, args: &[&str]) -> Command {
    let mut cmd = Command::new(paths.python());
    cmd.args(["-m", "scribe.installer"]).args(args).current_dir(paths.backend()).envs(python_env(paths));
    cmd
}

/// python -m scribe.installer <args> 를 실행하고 마지막 JSON 줄을 돌려준다
pub fn run_installer(paths: &Paths, args: &[&str]) -> Result<Value, String> {
    let out = installer_cmd(paths, args).stdin(Stdio::null()).output().map_err(|e| format!("실행하지 못했어요: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    text.lines()
        .rev()
        .find_map(|l| serde_json::from_str::<Value>(l).ok())
        .ok_or_else(|| {
            let err = String::from_utf8_lossy(&out.stderr);
            format!("결과를 읽지 못했어요: {}", err.lines().rev().take(6).collect::<Vec<_>>().join("\n"))
        })
}

/// 설치 전체. 끝나면 설정에 넣을 값(setup_done, packages_rev)을 돌려준다.
pub fn run(paths: &Paths, cfg: &Config, cancel: &Cancel, emit: &dyn Fn(Event)) -> Result<Config, String> {
    cancel.reset();
    fs::create_dir_all(paths.logs()).map_err(|e| e.to_string())?;
    let log = paths.logs().join("install.log");
    let rev = packages_rev(paths);

    // 1) 파이썬
    if python_works(paths) {
        emit(Event::new("python", "skip", Some(1.0), "파이썬이 준비돼 있어요"));
    } else {
        emit(Event::new("python", "running", None, "앱 전용 파이썬을 준비하는 중 (처음 한 번, 1분 안팎)"));
        let uv = local_uv(paths).map_err(|e| fail(emit, "python", e))?;
        let _ = fs::remove_dir_all(paths.venv());
        let mut cmd = Command::new(uv);
        cmd.args(["venv", "--python", PYTHON_VERSION, "--python-preference", "only-managed", "--seed"])
            .arg(paths.venv())
            .envs(uv_env(paths));
        run_lines(cmd, cancel, &log, |l| {
            if l.contains("Downloading") || l.contains("Using") {
                emit(Event::new("python", "running", None, l.trim().to_string()));
            }
        })
        .map_err(|e| fail(emit, "python", format!("파이썬을 준비하지 못했어요. 인터넷 연결을 확인해 주세요.\n{e}")))?;
        emit(Event::new("python", "done", Some(1.0), "파이썬을 준비했어요"));
    }

    // 2) 패키지
    if config::get_str(cfg, "packages_rev") == Some(rev.as_str()) && config::get_bool(cfg, "setup_done", false) {
        emit(Event::new("packages", "skip", Some(1.0), "패키지가 최신이에요"));
    } else {
        emit(Event::new("packages", "running", Some(0.02), "받아쓰기에 필요한 패키지를 받는 중 (약 1GB)"));
        let uv = local_uv(paths).map_err(|e| fail(emit, "packages", e))?;
        let mut cmd = Command::new(uv);
        cmd.args(["pip", "install", "--python"])
            .arg(paths.python())
            .arg("-r")
            .arg(paths.requirements())
            .envs(uv_env(paths));
        let mut prog = UvProgress::default();
        run_lines(cmd, cancel, &log, |l| {
            if prog.feed(l) {
                emit(Event::new("packages", "running", Some(prog.progress.max(0.02)), prog.message.clone()));
            }
        })
        .map_err(|e| fail(emit, "packages", format!("패키지를 설치하지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.\n{e}")))?;
        emit(Event::new("packages", "done", Some(1.0), "패키지 설치를 마쳤어요"));
    }

    // 3) 모델
    emit(Event::new("models", "running", Some(0.0), "받아쓰기 모델을 확인하는 중"));
    let mut total: f64 = 0.0;
    let mut result: Option<Value> = None;
    run_lines(installer_cmd(paths, &["download"]), cancel, &log, |l| {
        let Ok(v) = serde_json::from_str::<Value>(l) else { return };
        match v.get("event").and_then(|e| e.as_str()) {
            Some("start") => {
                total = v.get("total").and_then(|t| t.as_f64()).unwrap_or(0.0);
                let gb = total / 1024f64.powi(3);
                emit(Event::new("models", "running", Some(0.0), format!("모델 {:.1}GB를 받는 중 (이미 받은 것은 건너뛰어요)", gb)).with(v));
            }
            Some("progress") => {
                let done = v.get("done").and_then(|t| t.as_f64()).unwrap_or(0.0);
                let frac = if total > 0.0 { (done / total).min(1.0) } else { 1.0 };
                let gb = 1024f64.powi(3);
                emit(Event::new("models", "running", Some(frac), format!("모델 받는 중 {:.1} / {:.1}GB", done / gb, total / gb)).with(v));
            }
            Some("done") => result = Some(v),
            _ => {}
        }
    })
    .or_else(|e| if result.is_some() { Ok(()) } else { Err(e) })
    .map_err(|e| fail(emit, "models", format!("모델을 받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.\n{e}")))?;
    let failed: Vec<Value> = result
        .as_ref()
        .and_then(|r| r.get("failed"))
        .and_then(|f| f.as_array())
        .cloned()
        .unwrap_or_default();
    if failed.iter().any(|f| f.get("required").and_then(|r| r.as_bool()).unwrap_or(false)) {
        return Err(fail(emit, "models", "받아쓰기 모델을 받지 못했어요. 다시 시도해 주세요.".into()));
    }
    if let Some(f) = failed.first() {
        let hint = f.get("hint").and_then(|h| h.as_str()).unwrap_or("");
        emit(Event::new("models", "warn", Some(1.0), format!("받아쓰기 모델은 받았어요. 화자 구분 모델은 받지 못했어요. {hint}")).with(Value::Array(failed.clone())));
    } else {
        emit(Event::new("models", "done", Some(1.0), "모델을 모두 받았어요"));
    }

    // 4) 점검
    emit(Event::new("check", "running", None, "설치를 점검하는 중"));
    match run_installer(paths, &["doctor"]) {
        Ok(v) => {
            let bad = v.get("ok").and_then(|o| o.as_bool()) == Some(false);
            emit(Event::new("check", if bad { "warn" } else { "done" }, Some(1.0), if bad { "점검에서 확인할 것이 있어요" } else { "모두 준비됐어요" }).with(v));
        }
        Err(e) => emit(Event::new("check", "warn", Some(1.0), format!("점검을 마치지 못했어요: {e}"))),
    }

    // 끝났다는 알림("all")은 부른 쪽이 설정을 저장한 뒤에 보낸다
    let mut updates = Config::new();
    updates.insert("setup_done".into(), Value::Bool(true));
    updates.insert("packages_rev".into(), Value::String(rev));
    Ok(updates)
}

fn fail(emit: &dyn Fn(Event), step: &str, message: String) -> String {
    emit(Event::new(step, "error", None, message.clone()));
    emit(Event::new("all", "error", None, message.clone()));
    message
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uv_progress_from_output() {
        let mut p = UvProgress::default();
        for l in [
            "Resolved 85 packages in 1.20s",
            "Downloading torch (70.2MiB)",
            "Downloading numpy (5.1MiB)",
            " Downloaded numpy",
            " Downloaded torch",
            "Prepared 85 packages in 30.1s",
        ] {
            p.feed(l);
        }
        assert!((p.progress - 0.9).abs() < 1e-9);
        p.feed("Installed 85 packages in 210ms");
        assert_eq!(p.progress, 1.0);
        assert!(!p.feed("something else"));
    }

    #[test]
    fn rev_changes_with_requirements() {
        let dir = std::env::temp_dir().join(format!("ms-rev-{}", std::process::id()));
        let backend = dir.join("app").join("backend");
        fs::create_dir_all(&backend).unwrap();
        fs::write(backend.join("requirements.txt"), "fastapi\n").unwrap();
        fs::write(backend.join("requirements-mac.txt"), "-r requirements.txt\nmlx\n").unwrap();
        let paths = Paths { support: dir.join("s"), resources: dir.clone() };
        let a = packages_rev(&paths);
        fs::write(backend.join("requirements.txt"), "fastapi\nnumpy\n").unwrap();
        assert_ne!(a, packages_rev(&paths));
        let mut cfg = Config::new();
        assert!(needs_install(&paths, &cfg));
        cfg.insert("setup_done".into(), Value::Bool(true));
        cfg.insert("packages_rev".into(), Value::String(packages_rev(&paths)));
        assert!(needs_install(&paths, &cfg)); // 파이썬이 없다
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn uv_copied_to_support_and_refreshed() {
        let dir = std::env::temp_dir().join(format!("ms-uv-{}", std::process::id()));
        let paths = Paths { support: dir.join("s"), resources: dir.join("r") };
        assert!(local_uv(&paths).is_err());
        fs::create_dir_all(paths.uv().parent().unwrap()).unwrap();
        fs::write(paths.uv(), "v1").unwrap();
        let local = local_uv(&paths).unwrap();
        assert!(local.starts_with(&paths.support));
        assert_eq!(fs::read(&local).unwrap(), b"v1");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&local).unwrap().permissions().mode() & 0o777, 0o755);
        }
        fs::write(paths.uv(), "v2").unwrap(); // 앱을 새 버전으로 바꾸면
        assert_eq!(fs::read(local_uv(&paths).unwrap()).unwrap(), b"v2");
        let _ = fs::remove_dir_all(dir);
    }
}
