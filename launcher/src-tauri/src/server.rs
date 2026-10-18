//! 회의 노트 서버(파이썬)를 뒤에서 켜고 끈다. 화면은 브라우저로 http://127.0.0.1:<포트> 를 연다.

use crate::config::{self, Config};
use crate::install::python_env;
use crate::paths::Paths;
use serde_json::{json, Value};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Default)]
pub struct Server {
    child: Option<Child>,
    pub port: u16,
    /// 다른 곳(예: 예전 자동 실행)에서 이미 켜 둔 서버를 쓰는 중
    pub external: bool,
    pub starting: bool,
    pub error: Option<String>,
}

/// 127.0.0.1의 주소를 GET으로 읽는다 (상태 확인용, 라이브러리 없이)
pub fn http_get(port: u16, path: &str, timeout: Duration) -> Option<(u16, String)> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, timeout).ok()?;
    s.set_read_timeout(Some(timeout)).ok()?;
    s.set_write_timeout(Some(timeout)).ok()?;
    write!(s, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n").ok()?;
    let mut buf = Vec::new();
    s.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).to_string();
    let code = text.split_whitespace().nth(1)?.parse().ok()?;
    let body = text.split_once("\r\n\r\n").map(|(_, b)| b.to_string()).unwrap_or_default();
    Some((code, body))
}

/// 이 포트에서 회의 노트 서버가 응답하는지
pub fn healthy(port: u16) -> bool {
    matches!(http_get(port, "/api/health", Duration::from_millis(800)), Some((200, body)) if body.contains("\"ok\":true"))
}

/// 녹음 중인 회의가 있는지 (끄기 전에 묻는다)
pub fn recording(port: u16) -> bool {
    matches!(http_get(port, "/api/meetings", Duration::from_millis(1500)), Some((200, body)) if body.contains("\"recording\":true"))
}

/// 프로세스와 그 자식들(원본 스크립트 작업 등)을 함께 끝낸다
pub fn kill_tree(pid: u32, force: bool) {
    #[cfg(unix)]
    {
        let sig = if force { "-KILL" } else { "-TERM" };
        let _ = Command::new("kill").args([sig, &format!("-{pid}")]).status();
        let _ = Command::new("kill").args([sig, &pid.to_string()]).status();
    }
    #[cfg(windows)]
    {
        let mut args = vec!["/PID".to_string(), pid.to_string(), "/T".to_string()];
        if force {
            args.push("/F".into());
        }
        let _ = Command::new("taskkill").args(args).status();
    }
}

impl Server {
    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    pub fn running(&mut self) -> bool {
        if let Some(child) = self.child.as_mut() {
            if let Ok(Some(_)) = child.try_wait() {
                self.child = None;
            }
        }
        self.child.is_some() || self.external
    }

    pub fn status(&mut self) -> Value {
        let running = self.running();
        let state = if self.starting {
            "starting"
        } else if running && healthy(self.port) {
            "running"
        } else if self.error.is_some() {
            "error"
        } else {
            "stopped"
        };
        json!({"state": state, "port": self.port, "url": self.url(), "external": self.external, "error": self.error})
    }

    /// 서버 프로세스를 띄운다. 응답할 때까지 기다리는 것은 wait_ready에서 (잠금을 오래 쥐지 않게).
    pub fn spawn(&mut self, paths: &Paths, cfg: &Config) -> Result<bool, String> {
        self.port = config::port(cfg);
        self.error = None;
        if self.running() && healthy(self.port) {
            return Ok(false);
        }
        if healthy(self.port) {
            // 이미 켜져 있는 회의 노트 서버(예: scripts/launchagent.sh)를 그대로 쓴다
            self.external = true;
            return Ok(false);
        }
        self.external = false;
        if !paths.python().exists() {
            return Err("아직 설치하지 않았어요".into());
        }
        fs::create_dir_all(paths.logs()).map_err(|e| e.to_string())?;
        let log_path = paths.logs().join("server.log");
        // 로그가 너무 커지면 새로 시작한다
        if fs::metadata(&log_path).map(|m| m.len() > 20 * 1024 * 1024).unwrap_or(false) {
            let _ = fs::rename(&log_path, paths.logs().join("server.old.log"));
        }
        let log = OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| e.to_string())?;
        let err = log.try_clone().map_err(|e| e.to_string())?;
        let mut cmd = Command::new(paths.python());
        cmd.args(["-m", "uvicorn", "scribe.main:app", "--host", "127.0.0.1", "--port", &self.port.to_string(), "--log-level", "warning"])
            .current_dir(paths.backend())
            .envs(python_env(paths))
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(err));
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        let child = cmd.spawn().map_err(|e| format!("서버를 켜지 못했어요: {e}"))?;
        self.child = Some(child);
        self.starting = true;
        Ok(true)
    }

    pub fn stop(&mut self) {
        self.starting = false;
        self.external = false;
        if let Some(mut child) = self.child.take() {
            kill_tree(child.id(), false);
            let deadline = Instant::now() + Duration::from_secs(8);
            while Instant::now() < deadline {
                if let Ok(Some(_)) = child.try_wait() {
                    return;
                }
                thread::sleep(Duration::from_millis(100));
            }
            kill_tree(child.id(), true);
            let _ = child.wait();
        }
    }

    /// 프로세스가 살아 있는지 (기다리는 동안 죽었으면 로그 끝부분을 돌려준다)
    pub fn exited(&mut self, paths: &Paths) -> Option<String> {
        let child = self.child.as_mut()?;
        match child.try_wait() {
            Ok(Some(status)) => {
                self.child = None;
                Some(format!("서버가 멈췄어요 ({status}).\n{}", tail(&paths.logs().join("server.log"), 8)))
            }
            _ => None,
        }
    }
}

/// 서버가 응답할 때까지 기다린다 (처음에는 패키지를 불러오느라 몇 초 걸린다)
pub fn wait_ready(server: &std::sync::Mutex<Server>, paths: &Paths, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    loop {
        let port = {
            let mut s = server.lock().unwrap();
            if let Some(err) = s.exited(paths) {
                s.starting = false;
                s.error = Some(err.clone());
                return Err(err);
            }
            s.port
        };
        if healthy(port) {
            server.lock().unwrap().starting = false;
            return Ok(());
        }
        if Instant::now() > deadline {
            let mut s = server.lock().unwrap();
            s.starting = false;
            let err = format!("서버가 응답하지 않아요.\n{}", tail(&paths.logs().join("server.log"), 8));
            s.error = Some(err.clone());
            return Err(err);
        }
        thread::sleep(Duration::from_millis(300));
    }
}

pub fn tail(path: &std::path::Path, lines: usize) -> String {
    let text = fs::read_to_string(path).unwrap_or_default();
    let all: Vec<&str> = text.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn http_get_reads_status_and_body() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let t = thread::spawn(move || {
            let (mut s, _) = l.accept().unwrap();
            // 요청을 끝까지 읽은 뒤 답한다 (덜 읽고 닫으면 연결이 끊긴 것으로 보일 수 있다)
            let mut req = Vec::new();
            let mut buf = [0u8; 256];
            while !req.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = s.read(&mut buf).unwrap();
                if n == 0 {
                    break;
                }
                req.extend_from_slice(&buf[..n]);
            }
            s.write_all(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"ok\":true,\"recording\":true}").unwrap();
        });
        let (code, body) = http_get(port, "/api/health", Duration::from_secs(2)).unwrap();
        t.join().unwrap();
        assert_eq!(code, 200);
        assert!(body.contains("\"ok\":true"));
        assert!(!healthy(1)); // 닫힌 포트
    }
}
