"""Portable meeting archives retain their audio references and reimport data."""

import io
import unicodedata
import wave
import zipfile
from html.parser import HTMLParser

import pytest

from scribe import config, meeting_file
from scribe.anchors import TextIndex, compute_anchor
from scribe.db import Store
from scribe.views import Views


class Frames(HTMLParser):
    def __init__(self):
        super().__init__()
        self.docs = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "iframe":
            self.docs[attrs["id"]] = attrs["srcdoc"]


@pytest.fixture
def exported_meeting(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    # Store installs a folder resolver globally; restore the previous resolver after this test.
    monkeypatch.setattr(config, "folder_of", config.folder_of)
    store = Store(tmp_path / "scribe.db")
    views = Views(store)
    meeting = store.create_meeting(unicodedata.normalize("NFD", "대학원 장학 회의"))
    mid = meeting["id"]
    segments = [store.add_live_segment(mid, i * 3 + .25, i * 3 + 1.75, f"논의 {i}번 확인할 내용을 기록합니다") for i in range(22)]
    index = TextIndex(views.units(mid))
    for i, segment in enumerate(segments):
        anchor = compute_anchor(index, {"unit": segment["id"], "offset": 0}, {"unit": segment["id"], "offset": 2})
        store.add_annotation(mid, kind="comment", anchor=anchor, body=f"댓글 {i} 확인 사항")
        store.add_annotation(mid, kind="memo", anchor={"t": i * 3 + 2.125, "after": segment["id"]}, body=f"메모 {i} 결정 사항")
    store.update_meeting(mid, status="recorded", duration=80.0, summary_md="## 한 줄 요약\n장학 업무 확인\n\n## 주요 논의\n- 업무 확인 [00:01]")
    audio = config.settings.meeting_dir(mid) / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 8000 * 80)
    name = meeting_file.base_name(meeting)
    return {"store": store, "views": views, "mid": mid, "name": name, "audio": audio, "tmp": tmp_path}


def archive(exported):
    return b"".join(meeting_file.iter_zip(exported["store"], exported["views"], exported["mid"], exported["name"]))


def test_archive_has_exact_portable_audio_paths_and_original_data(exported_meeting):
    exported = exported_meeting
    with zipfile.ZipFile(io.BytesIO(archive(exported))) as zf:
        name = exported["name"]
        assert unicodedata.is_normalized("NFC", name)
        assert set(zf.namelist()) == {f"{name}/meeting.html", f"{name}/recording.wav", f"{name}/README.txt"}
        assert zf.read(f"{name}/recording.wav") == exported["audio"].read_bytes()
        assert zf.testzip() is None
        page = zf.read(f"{name}/meeting.html").decode("utf-8")
        frames = Frames()
        frames.feed(page)
        assert set(frames.docs) == {"f-transcript", "f-minutes"}
        for document in frames.docs.values():
            assert 'src="recording.wav"' in document
            assert "https://cdn.jsdelivr.net" not in document
        data = meeting_file.parse_html(page)
        assert len(data["segments"]) == 22 and len(data["annotations"]) == 44
        assert "모두 추출" in zf.read(f"{name}/README.txt").decode("utf-8-sig")
    imported, audio = meeting_file.read_files([("meeting.zip", io.BytesIO(archive(exported)))])
    assert imported["meeting"]["title"] == data["meeting"]["title"]
    assert audio[0] == ".wav" and audio[1].read() == exported["audio"].read_bytes()
    audio[1].close()


def test_m4a_keeps_format_and_bytes(exported_meeting):
    exported = exported_meeting
    exported["audio"].unlink()
    m4a = exported["audio"].with_suffix(".m4a")
    m4a.write_bytes(b"m4a test bytes")
    with zipfile.ZipFile(io.BytesIO(archive(exported))) as zf:
        path = f'{exported["name"]}/recording.m4a'
        assert zf.read(path) == m4a.read_bytes()
        assert zf.getinfo(path).compress_type == zipfile.ZIP_STORED
        page = zf.read(f'{exported["name"]}/meeting.html').decode("utf-8")
        frames = Frames()
        frames.feed(page)
        assert all('src="recording.m4a"' in doc for doc in frames.docs.values())


def test_no_audio_export_is_still_readable(exported_meeting):
    exported = exported_meeting
    exported["audio"].unlink()
    with zipfile.ZipFile(io.BytesIO(archive(exported))) as zf:
        assert len(zf.namelist()) == 2
        frames = Frames()
        frames.feed(zf.read(f'{exported["name"]}/meeting.html').decode("utf-8"))
        assert all('<audio' not in doc for doc in frames.docs.values())
        assert "녹음 파일이 포함되어 있지 않습니다" in zf.read(f'{exported["name"]}/README.txt').decode("utf-8-sig")


def test_name_normalization_and_trailing_windows_characters():
    name = meeting_file.base_name({"created_at": 1_760_000_000, "title": unicodedata.normalize("NFD", "한글") + "<>:" + "a" * 74 + " . 더 뒤"})
    assert unicodedata.is_normalized("NFC", name)
    assert not any(c in name for c in '<>:')
    assert not name.endswith((" ", "."))
