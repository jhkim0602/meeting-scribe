//! 파일 위치: 앱에 들어 있는 것(resources)과 설치하며 만드는 것(support)

use std::path::PathBuf;

#[derive(Clone, Debug)]
pub struct Paths {
    /// 설치하며 만드는 것: 파이썬, 패키지, 설정, 로그 (~/Library/Application Support/com.meetingscribe.app)
    pub support: PathBuf,
    /// 앱에 들어 있는 것: 서버 코드, 화면, uv
    pub resources: PathBuf,
}

impl Paths {
    pub fn backend(&self) -> PathBuf {
        self.resources.join("app").join("backend")
    }

    /// 회의 노트 화면 (frontend/dist)
    pub fn frontend(&self) -> PathBuf {
        self.resources.join("app").join("frontend")
    }

    pub fn uv(&self) -> PathBuf {
        self.resources.join("bin").join(if cfg!(windows) { "uv.exe" } else { "uv" })
    }

    pub fn venv(&self) -> PathBuf {
        self.support.join("venv")
    }

    pub fn python(&self) -> PathBuf {
        if cfg!(windows) {
            self.venv().join("Scripts").join("python.exe")
        } else {
            self.venv().join("bin").join("python")
        }
    }

    pub fn config_file(&self) -> PathBuf {
        self.support.join("config.json")
    }

    pub fn logs(&self) -> PathBuf {
        self.support.join("logs")
    }

    /// 이 기기에서 설치할 패키지 목록 (맥은 MLX 받아쓰기까지)
    pub fn requirements(&self) -> PathBuf {
        let name = if cfg!(target_os = "macos") { "requirements-mac.txt" } else { "requirements.txt" };
        self.backend().join(name)
    }

    /// 기본 저장 위치 (~/MeetingScribe)
    pub fn default_data_dir() -> PathBuf {
        home().join("MeetingScribe")
    }
}

pub fn home() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}
