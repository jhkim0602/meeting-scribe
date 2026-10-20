"""회의 파일: 회의 하나를 '날짜_제목' 폴더(HTML 하나 + 녹음)로 내보내고, 그 파일을 다시 불러온다.

  2026-10-20_주간회의/
    2026-10-20_주간회의.html   전사 기록·회의록 탭 + 불러오기용 원본 데이터(보이지 않는 JSON)
    2026-10-20_주간회의.m4a    같은 폴더에 있으면 HTML에서 시각을 눌러 들을 수 있다

브라우저로 열면 보기만 하고, 앱에서 불러오면 보통 회의처럼 이어서 고칠 수 있다.
불러올 때는 문장·메모 id를 새로 만들어, 같은 파일을 여러 번 불러와도 서로 섞이지 않는다.
"""

from __future__ import annotations

import html
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Iterator, Optional

from . import minutes, transcript_html
from .backup import _add_file, _Sink
from .config import settings
from .db import Store, new_id
from .storage import audio_file
from .views import Views

FORMAT = "meeting-scribe/1"
DATA_ID = "meeting-scribe-data"
MEETING_COLS = ("title", "created_at", "source", "duration", "original_name", "num_speakers", "speakers",
                "participants", "speaker_map", "clock", "summary_md", "summary_updated_at",
                "notes_json", "notes_md", "notes_updated_at")
SEG_COLS = ("id", "idx", "t0", "t1", "text", "created_at", "speaker", "speaker_auto", "speaker_src",
            "speaker_conf", "text_orig", "edited_at")
ANN_COLS = ("id", "kind", "origin", "origin_version", "anchor", "body", "replacement", "color", "status",
            "replies", "meta", "created_at", "updated_at")
AUDIO_EXTS = (".m4a", ".wav")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class MeetingFileError(ValueError):
    """사용자에게 그대로 보여 줄 불러오기 실패 이유"""


def base_name(m: dict) -> str:
    """2026-10-20_주간회의 (파일 이름에 못 쓰는 글자는 빼고)"""
    day = time.strftime("%Y-%m-%d", time.localtime(m["created_at"]))
    title = " ".join(_UNSAFE.sub(" ", m.get("title") or "").split()).strip(".")[:80] or "회의"
    return f"{day}_{title}"


# ---------------- 내보내기 ----------------

def collect(store: Store, meeting_id: str) -> Optional[dict]:
    """불러오기에 필요한 원본: 회의 정보, 기록 문장(화자·고친 글자 포함), 메모·댓글·강조, 원본 스크립트"""
    with store.conn() as c:
        m = c.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if m is None:
            return None
        segs = c.execute(f"SELECT {','.join(SEG_COLS)} FROM live_segments WHERE meeting_id=? ORDER BY idx", (meeting_id,)).fetchall()
        anns = c.execute(f"SELECT {','.join(ANN_COLS)} FROM annotations WHERE meeting_id=? ORDER BY created_at", (meeting_id,)).fetchall()
    final = store.get_final(meeting_id)
    return {
        "format": FORMAT,
        "exported_at": time.time(),
        "meeting": {k: m[k] for k in MEETING_COLS},
        "segments": [dict(r) for r in segs],
        "annotations": [dict(r) for r in anns],
        "final": final,
    }


def render(store: Store, views: Views, meeting_id: str, audio_name: Optional[str]) -> Optional[str]:
    """보기 좋은 HTML 하나: 위쪽 탭으로 전사 기록과 회의록을 오가고, 원본 데이터는 숨겨 둔다."""
    data = collect(store, meeting_id)
    t_data = transcript_html.collect(store, views, meeting_id)
    m_data = minutes.collect(store, views, meeting_id)
    if data is None or t_data is None or m_data is None:
        return None
    docs = {"transcript": transcript_html.render(t_data, audio_src=audio_name), "minutes": minutes.render(m_data, audio_src=audio_name)}
    title = html.escape(data["meeting"]["title"] or "회의")
    day = time.strftime("%Y. %m. %d.", time.localtime(data["meeting"]["created_at"]))
    frames = "".join(
        f'<iframe id="f-{k}" title="{label}" srcdoc="{html.escape(docs[k], quote=True)}"{"" if k == "transcript" else " hidden"}></iframe>'
        for k, label in (("transcript", "전사 기록"), ("minutes", "회의록"))
    )
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="{FORMAT}">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; --line: #e5e5e5; --ink: #1a1a1a; --mute: #6b6b6b; --on: #1a1a1a; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: #fff; color: var(--ink);
    font-family: Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif; }}
  body {{ display: flex; flex-direction: column; }}
  header {{ display: flex; align-items: center; gap: 12px; padding: 10px 16px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }}
  header h1 {{ font-size: 15px; margin: 0; font-weight: 600; }}
  header .day {{ color: var(--mute); font-size: 13px; }}
  nav {{ display: flex; gap: 4px; margin-left: auto; }}
  nav button {{ font: inherit; font-size: 13px; padding: 6px 12px; border: 1px solid var(--line); background: #fff; border-radius: 6px; cursor: pointer; }}
  nav button[aria-pressed="true"] {{ background: var(--on); color: #fff; border-color: var(--on); }}
  iframe {{ flex: 1; width: 100%; border: 0; }}
  iframe[hidden] {{ display: none; }}
  @media print {{ header {{ display: none; }} }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1><span class="day">{day}</span>
  <nav>
    <button type="button" data-tab="transcript" aria-pressed="true">전사 기록</button>
    <button type="button" data-tab="minutes" aria-pressed="false">회의록</button>
    <button type="button" id="print">인쇄 · PDF</button>
  </nav>
</header>
{frames}
<script type="application/json" id="{DATA_ID}">{payload}</script>
<script>
(function () {{
  var tabs = document.querySelectorAll('nav [data-tab]');
  tabs.forEach(function (b) {{
    b.addEventListener('click', function () {{
      tabs.forEach(function (o) {{
        var on = o === b;
        o.setAttribute('aria-pressed', on ? 'true' : 'false');
        document.getElementById('f-' + o.dataset.tab).hidden = !on;
      }});
    }});
  }});
  document.getElementById('print').addEventListener('click', function () {{
    var f = document.querySelector('iframe:not([hidden])');
    if (f && f.contentWindow) f.contentWindow.print();
  }});
}})();
</script>
</body>
</html>
"""


def iter_zip(store: Store, views: Views, meeting_id: str, name: str) -> Iterator[bytes]:
    """name/name.html + name/name.m4a 를 zip으로 흘려보낸다 (녹음은 이미 압축돼 있어 그대로)"""
    audio = audio_file(settings.meetings_dir / meeting_id)
    page = render(store, views, meeting_id, f"{name}{audio.suffix}" if audio else None) or ""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        zf.writestr(zipfile.ZipInfo(f"{name}/{name}.html", date_time=time.localtime()[:6]), page.encode("utf-8"),
                    compress_type=zipfile.ZIP_DEFLATED)
        yield sink.take()
        if audio is not None:
            yield from _add_file(zf, sink, audio, f"{name}/{name}{audio.suffix}", compress=False)
    yield sink.take()


# ---------------- 불러오기 ----------------

_DATA_RE = re.compile(r'<script type="application/json" id="' + DATA_ID + r'">(.*?)</script>', re.S)


def parse_html(text: str) -> dict:
    found = _DATA_RE.search(text)
    if not found:
        raise MeetingFileError("회의 노트에서 내보낸 HTML이 아니에요")
    try:
        data = json.loads(found.group(1))
    except ValueError:
        raise MeetingFileError("파일 안의 회의 데이터가 깨졌어요")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise MeetingFileError("이 버전의 앱에서 읽을 수 없는 회의 파일이에요")
    return data


def read_files(files: list[tuple[str, Any]]) -> tuple[dict, Optional[tuple[str, Any]]]:
    """올린 파일들(zip 하나, 또는 HTML + 녹음)에서 (회의 데이터, (녹음 확장자, 파일 객체)) 를 찾는다."""
    page: Optional[str] = None
    audio: Optional[tuple[str, Any]] = None
    for name, f in files:
        ext = Path(name).suffix.lower()
        if ext == ".zip":
            try:
                zf = zipfile.ZipFile(f)
            except zipfile.BadZipFile:
                raise MeetingFileError("zip 파일을 열지 못했어요")
            for info in zf.infolist():
                iext = Path(info.filename).suffix.lower()
                if iext == ".html" and page is None:
                    page = zf.read(info).decode("utf-8", errors="replace")
                elif iext in AUDIO_EXTS and audio is None:
                    audio = (iext, zf.open(info))
        elif ext in (".html", ".htm") and page is None:
            page = f.read().decode("utf-8", errors="replace")
        elif ext in AUDIO_EXTS and audio is None:
            audio = (ext, f)
    if page is None:
        raise MeetingFileError("회의 HTML 파일이 없어요 (내보낸 zip이나 .html을 올려 주세요)")
    return parse_html(page), audio


def _remap(value: Any, ids: dict[str, str]) -> Any:
    """메모·댓글 위치 안의 옛 문장 id를 새 id로"""
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [_remap(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _remap(v, ids) for k, v in value.items()}
    return value


def import_meeting(store: Store, data: dict, audio: Optional[tuple[str, Any]] = None) -> dict:
    """새 회의로 만든다. 녹음이 같이 오면 회의 폴더에 넣는다."""
    src = data.get("meeting") or {}
    seg_ids = {s["id"]: new_id() for s in data.get("segments") or []}
    m = store.create_meeting(src.get("title") or "불러온 회의", source=src.get("source") or "live", original_name=src.get("original_name"))
    mid = m["id"]
    fields = {k: src.get(k) for k in MEETING_COLS if k not in ("title", "source", "original_name") and src.get(k) is not None}
    final = data.get("final")
    with store.conn() as c:
        c.execute("BEGIN")
        try:
            if fields:
                c.execute(f"UPDATE meetings SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), mid])
            c.executemany(
                f"INSERT INTO live_segments(meeting_id,{','.join(SEG_COLS)}) VALUES(?{',?' * len(SEG_COLS)})",
                [(mid, *[seg_ids[s["id"]] if k == "id" else s.get(k) for k in SEG_COLS]) for s in data.get("segments") or []],
            )
            rows = []
            for a in data.get("annotations") or []:
                a = dict(a, id=new_id())
                a["anchor"] = json.dumps(_remap(json.loads(a["anchor"]), seg_ids), ensure_ascii=False)
                rows.append((mid, *[a.get(k) for k in ANN_COLS]))
            c.executemany(f"INSERT INTO annotations(meeting_id,{','.join(ANN_COLS)}) VALUES(?{',?' * len(ANN_COLS)})", rows)
            if final and final.get("data"):
                c.execute("INSERT INTO final_versions(meeting_id,version,created_at,engine,diarizer,data) VALUES(?,?,?,?,?,?)",
                          (mid, 1, final.get("created_at") or time.time(), final.get("engine"), final.get("diarizer"),
                           json.dumps(final["data"], ensure_ascii=False)))
            c.execute("UPDATE meetings SET status=?, final_version=?, record_rev=record_rev+1 WHERE id=?",
                      ("ready" if final else "recorded", 1 if final else 0, mid))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            store.delete_meeting(mid)
            raise
    store.reindex_meeting(mid)
    if audio is not None:
        ext, f = audio
        dst = settings.meeting_dir(mid) / f"audio{ext}"
        with open(dst, "wb") as out:
            while chunk := f.read(1 << 20):
                out.write(chunk)
    return store.get_meeting(mid)  # type: ignore[return-value]
