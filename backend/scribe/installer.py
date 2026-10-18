"""설치 도우미(앱)가 부르는 명령: python -m scribe.installer <명령> (결과는 한 줄에 JSON 하나)

  catalog              이 맥 정보, 모델 목록, 추천 선택
  download             고른 모델(+화자 구분 모델) 내려받기. 진행률을 이어서 알린다
  check-hf             Hugging Face 토큰과 화자 구분 모델 약관 동의 확인
  claude [connect|disconnect|status]   Claude 데스크톱 앱에 MCP 서버로 연결
  doctor               설치 점검 (패키지, ffmpeg, 모델, 저장 위치, 연결)

설정은 앱이 넘겨주는 SCRIBE_CONFIG(config.json)에서 읽는다 (config.py).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import catalog
from .config import settings

Emit = Callable[[dict], None]
MCP_NAME = "meeting-scribe"
OLD_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.meeting-scribe.server.plist"
HF = "https://huggingface.co"


def emit_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


# ---------------- 이 맥 ----------------

def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def ram_gb() -> Optional[float]:
    mem = _sh(["sysctl", "-n", "hw.memsize"])
    if mem.isdigit():
        return round(int(mem) / catalog.GB, 1)
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) * 1024 / catalog.GB, 1)
    except OSError:
        pass
    return None


def free_gb(path: Path) -> Optional[float]:
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return round(shutil.disk_usage(p).free / catalog.GB, 1)
    except OSError:
        return None


def machine() -> dict:
    chip = _sh(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor() or platform.machine()
    ram = ram_gb()
    return {
        "chip": chip, "ram_gb": ram, "os": f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else platform.platform(),
        "apple_silicon": sys.platform == "darwin" and platform.machine() == "arm64",
        "data_dir": str(settings.data_dir), "free_gb": free_gb(settings.data_dir), "recommend": catalog.recommend(ram),
    }


# ---------------- 모델 내려받기 ----------------

def download_targets(token: Optional[str]) -> tuple[list[str], list[str]]:
    """(받아쓰기 모델, 화자 구분 모델). 화자 구분은 토큰이 있고 pyannote가 설치돼 있을 때만."""
    from .engines import repos_for

    models: list[str] = []
    for role in ("live", "final"):
        models += [r for r in repos_for(role) if r not in models]
    speaker: list[str] = []
    if token:
        import importlib.util

        if importlib.util.find_spec("pyannote.audio") is not None or importlib.util.find_spec("pyannote") is not None:
            speaker = list(catalog.CATALOG["speaker"]["repos"])
    return models, speaker


def download(emit: Emit = emit_json, snapshot: Optional[Callable] = None, every: float = 0.5) -> int:
    from .engines.download import MODEL_SIZES, downloaded_bytes

    token = settings.hf_token or os.environ.get("HF_TOKEN") or None
    models, speaker = download_targets(token)
    repos = models + speaker
    if snapshot is None and repos:
        from huggingface_hub import snapshot_download as snapshot  # type: ignore
    total = sum(MODEL_SIZES.get(r, 0) for r in repos)
    emit({"event": "start", "repos": repos, "total": total})
    state = {"repo": None}
    stop = threading.Event()

    def done_bytes() -> int:
        return sum(min(downloaded_bytes(r), MODEL_SIZES.get(r, 0)) for r in repos)

    def watch() -> None:
        while not stop.wait(every):
            emit({"event": "progress", "done": done_bytes(), "total": total, "repo": state["repo"]})

    threading.Thread(target=watch, daemon=True).start()
    failed = []
    try:
        for repo in repos:
            state["repo"] = repo
            try:
                snapshot(repo, token=token if repo in speaker else None)
            except Exception as exc:  # noqa: BLE001
                hint = f"{catalog.CATALOG['speaker']['terms_url']} 에서 약관에 동의했는지 확인해 주세요." if repo in speaker else "인터넷 연결을 확인하고 다시 시도해 주세요."
                failed.append({"repo": repo, "required": repo in models, "message": f"{type(exc).__name__}: {exc}", "hint": hint})
    finally:
        stop.set()
    emit({"event": "progress", "done": done_bytes(), "total": total, "repo": None})
    ok = not any(f["required"] for f in failed)
    emit({"event": "done", "ok": ok, "failed": failed})
    return 0 if ok else 1


# ---------------- Hugging Face 토큰 ----------------

def _request(url: str, token: str, method: str = "GET", timeout: float = 15.0):
    req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {token}", "User-Agent": "meeting-scribe"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - 고정된 https 주소


def check_hf(token: Optional[str], opener: Callable = _request) -> dict:
    """토큰이 맞는지, 화자 구분 모델 약관에 동의했는지"""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "valid": False, "access": False, "message": "토큰이 비어 있어요"}
    try:
        with opener(f"{HF}/api/whoami-v2", token) as r:
            user = json.loads(r.read().decode() or "{}").get("name")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "valid": False, "access": False, "message": "토큰이 맞지 않아요. 다시 복사해 붙여 넣어 주세요."}
        return {"ok": False, "valid": None, "access": False, "message": f"Hugging Face에 연결하지 못했어요 ({e.code})"}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "valid": None, "access": False, "message": f"인터넷 연결을 확인해 주세요 ({e})"}
    repo = catalog.CATALOG["speaker"]["repos"][0]
    try:
        with opener(f"{HF}/{repo}/resolve/main/config.yaml", token, method="HEAD"):
            pass
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "valid": True, "access": False, "user": user,
                    "message": "토큰은 맞아요. 화자 구분 모델 페이지에서 약관에 동의해 주세요.", "terms_url": catalog.CATALOG["speaker"]["terms_url"]}
        return {"ok": False, "valid": True, "access": None, "user": user, "message": f"약관 동의를 확인하지 못했어요 ({e.code})"}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "valid": True, "access": None, "user": user, "message": f"인터넷 연결을 확인해 주세요 ({e})"}
    return {"ok": True, "valid": True, "access": True, "user": user, "message": f"{user or '토큰'} 확인했어요. 화자 구분을 쓸 수 있어요."}


# ---------------- Claude 데스크톱 연결 ----------------

def claude_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def mcp_entry() -> dict:
    """Claude가 이 앱의 회의 기록을 읽는 MCP 서버 실행 방법 (앱에 설치된 파이썬과 코드)"""
    backend = Path(__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(backend), "PYTHONDONTWRITEBYTECODE": "1"}
    if os.environ.get("SCRIBE_CONFIG"):
        env["SCRIBE_CONFIG"] = os.environ["SCRIBE_CONFIG"]
    else:
        env["SCRIBE_DATA_DIR"] = str(settings.data_dir)
    return {"command": sys.executable, "args": ["-m", "scribe.mcp_server"], "env": env}


def claude(action: str = "status", path: Optional[Path] = None) -> dict:
    path = path or claude_config_path()
    installed = path.parent.exists()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {"ok": False, "connected": False, "installed": installed, "path": str(path),
                "message": "Claude 설정 파일을 읽지 못했어요. Claude 앱의 설정 → 개발자에서 확인해 주세요."}
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
    entry = mcp_entry()
    changed = False
    if action == "connect" and servers.get(MCP_NAME) != entry:
        servers[MCP_NAME] = entry
        changed = True
    elif action == "disconnect" and MCP_NAME in servers:
        servers.pop(MCP_NAME)
        changed = True
    if changed:
        data["mcpServers"] = servers
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(".json.bak"))  # 사용자가 넣어 둔 다른 연결을 잃지 않게
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    connected = MCP_NAME in servers
    msg = ("Claude 데스크톱에 연결했어요. Claude 앱을 다시 켜면 회의 노트가 보여요." if changed and connected
           else "연결을 끊었어요." if changed else "연결돼 있어요." if connected else "아직 연결하지 않았어요.")
    if connected and not installed:
        msg += " (Claude 데스크톱 앱을 설치하면 바로 쓸 수 있어요)"
    return {"ok": True, "connected": connected, "changed": changed, "installed": installed, "path": str(path), "message": msg}


# ---------------- 점검 ----------------

def doctor() -> list[dict]:
    import importlib.util

    from .audio import ffmpeg_path
    from .engines import model_for, repos_for, resolve_diarizer_kind
    from .engines.download import DownloadWatch

    def has(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            return False

    items: list[dict] = []

    def add(id_: str, ok: Optional[bool], label: str, detail: str = "") -> None:
        items.append({"id": id_, "ok": ok, "label": label, "detail": detail})

    add("python", sys.version_info >= (3, 11), "파이썬", platform.python_version())
    engine_pkg = {"qwen3": "mlx_qwen3_asr", "whisper": "mlx_whisper", "faster-whisper": "faster_whisper"}
    for role, name in (("live", "실시간 받아쓰기"), ("final", "원본 스크립트")):
        kind, model = model_for(role)
        pkg = engine_pkg.get(kind)
        if pkg and not has(pkg):
            add(f"{role}-engine", False, name, f"{kind} 패키지가 없어요")
            continue
        repos = repos_for(role)
        cached = (not DownloadWatch(repos).needed) if repos else True
        add(f"{role}-model", True if cached else None, name, f"{model}" + ("" if cached else " · 아직 받지 않았어요 (처음 쓸 때 받아요)"))
    ff = ffmpeg_path()
    add("ffmpeg", bool(ff), "오디오 변환(ffmpeg)", ff or "없음")
    token = settings.hf_token or os.environ.get("HF_TOKEN")
    kind = resolve_diarizer_kind()
    add("speaker", True if kind not in ("none",) else None, "화자 구분", kind if kind != "none" else ("토큰이 없어요 (선택)" if not token else "모델을 아직 받지 않았어요"))
    data = settings.data_dir
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    free = free_gb(data)
    add("data", writable and (free is None or free > 2), "저장 위치", f"{data} · 남은 공간 {free if free is not None else '?'}GB")
    c = claude("status")
    add("claude", True if c["connected"] else None, "Claude 데스크톱 연결", c["message"])
    if OLD_AGENT.exists():
        add("old-agent", None, "예전 자동 실행", "scripts/launchagent.sh로 켠 자동 실행이 있어요. 앱의 '로그인할 때 켜기'를 쓰면 'scripts/launchagent.sh uninstall'로 꺼 주세요")
    return items


# ---------------- 명령 ----------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scribe.installer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog")
    sub.add_parser("download")
    hf = sub.add_parser("check-hf")
    hf.add_argument("--token", default=None, help="없으면 HF_TOKEN")
    cl = sub.add_parser("claude")
    cl.add_argument("action", nargs="?", default="status", choices=["status", "connect", "disconnect"])
    sub.add_parser("doctor")
    args = ap.parse_args(argv)
    if args.cmd == "catalog":
        emit_json({"machine": machine(), "catalog": catalog.CATALOG})
        return 0
    if args.cmd == "download":
        return download()
    if args.cmd == "check-hf":
        r = check_hf(args.token if args.token is not None else (settings.hf_token or os.environ.get("HF_TOKEN")))
        emit_json(r)
        return 0 if r["ok"] else 1
    if args.cmd == "claude":
        r = claude(args.action)
        emit_json(r)
        return 0 if r["ok"] else 1
    if args.cmd == "doctor":
        items = doctor()
        emit_json({"items": items, "ok": all(i["ok"] is not False for i in items), "at": time.time()})
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
