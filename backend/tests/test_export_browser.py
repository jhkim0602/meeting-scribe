"""Run the exported files themselves in Chromium, offline, on every supported OS."""

import io
import json
import os
import shutil
import subprocess
import zipfile

import pytest
from playwright.sync_api import expect, sync_playwright

from scribe import meeting_file
from test_meeting_export import archive, exported_meeting  # noqa: F401


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        options = {"headless": True, "args": ["--mute-audio"]}
        if os.environ.get("SCRIBE_CHROMIUM_PATH"):
            options["executable_path"] = os.environ["SCRIBE_CHROMIUM_PATH"]
        elif os.environ.get("SCRIBE_BROWSER_CHANNEL"):
            options["channel"] = os.environ["SCRIBE_BROWSER_CHANNEL"]
        browser = playwright.chromium.launch(**options)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, offline=True, accept_downloads=True)
    page = context.new_page()
    errors, external = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: external.append(request.url) if request.url.startswith(("https://", "http://")) else None)
    yield page
    context.close()
    assert not errors, errors
    assert not external, external


def open_export(page, exported, *, missing_audio=False):
    output = exported["tmp"] / "extracted"
    with zipfile.ZipFile(io.BytesIO(archive(exported))) as zf:
        zf.extractall(output)
    path = output / exported["name"] / "meeting.html"
    if missing_audio:
        path.with_name("recording.wav").unlink()
    page.goto(path.as_uri(), wait_until="load")
    return path


def transcript(page):
    return page.locator("#f-transcript").content_frame


def minutes(page):
    return page.locator("#f-minutes").content_frame


def audio_state(frame):
    return frame.locator("#au").evaluate("a => ({time: a.currentTime, paused: a.paused})")


def edit(frame, selector, value):
    frame.locator("#fe-toggle").click()
    element = frame.locator(selector).first
    element.dblclick()
    element.fill(value)
    element.press("Enter")
    frame.locator("#fe-toggle").click()


def test_every_annotation_target_seeks_and_plays_without_frame_navigation(page, exported_meeting):
    open_export(page, exported_meeting)
    frame = transcript(page)
    frame.locator(".outline-list .jump").first.click()
    expect(frame.locator("#pp")).to_have_class("pp on")
    result = frame.locator("body").evaluate("""async () => {
      const audio = document.querySelector('#au');
      const payload = JSON.parse(parent.document.querySelector('#meeting-scribe-data').textContent);
      const times = Object.fromEntries(payload.annotations.map(note => {
        const anchor = JSON.parse(note.anchor); return [note.id, anchor.t ?? anchor.t0];
      }));
      const counts = {};
      for (const [kind, selector] of [
        ['links', '.jump[href]'], ['memos', '.memo[data-ann] .ann-body'],
        ['comments', '.cmt[data-ann] .ann-body'], ['numbers', '.cn[href]'], ['marks', '.cm[data-c]']
      ]) {
        for (const element of document.querySelectorAll(selector)) {
          let note = element.closest('[data-ann]');
          if (element.matches('.jump,.cn')) note = document.getElementById(element.getAttribute('href').slice(1));
          if (element.matches('.cm')) note = document.getElementById('c-' + element.dataset.c);
          const time = times[note.dataset.ann]; audio.pause();
          const seeked = new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(Error('seek timeout: ' + kind)), 3000);
            audio.addEventListener('seeked', () => { clearTimeout(timer); resolve(); }, {once:true});
          });
          element.click(); await seeked;
          if (audio.paused || Math.abs(audio.currentTime - time) > .5) throw Error(kind + ' incorrect playback');
          if (location.href !== 'about:srcdoc') throw Error('frame navigated');
          counts[kind] = (counts[kind] || 0) + 1;
        }
      }
      audio.pause(); return counts;
    }""")
    assert result == {"links": 88, "memos": 22, "comments": 22, "numbers": 22, "marks": 22}


def test_tab_switches_transfer_playback_and_pause_hidden_audio(page, exported_meeting):
    open_export(page, exported_meeting)
    frame = transcript(page)
    frame.locator(".outline-list .jump").nth(6).click()
    expect(frame.locator("#pp")).to_have_class("pp on")
    before = audio_state(frame)["time"]
    page.locator('[data-tab="minutes"]').click()
    expect(minutes(page).locator("#pp")).to_have_class("pp on")
    assert audio_state(frame)["paused"]
    assert abs(audio_state(minutes(page))["time"] - before) < 2
    minutes(page).locator("#pp").click()
    page.locator('[data-tab="transcript"]').click()
    assert audio_state(frame)["paused"]


def test_drafts_are_isolated_and_saved_html_keeps_both_tabs_and_reimport_data(page, exported_meeting):
    page.add_init_script("Object.defineProperty(window, 'showSaveFilePicker', {value: undefined})")
    original = open_export(page, exported_meeting)
    payload = page.locator("#meeting-scribe-data").text_content()
    edit(transcript(page), ".s", "수정한 전사 문장")
    page.locator('[data-tab="minutes"]').click()
    edit(minutes(page), "main h1", "수정한 회의록 제목")
    keys = page.evaluate("Object.keys(localStorage)")
    assert len(keys) == 2 and any(k.endswith(":transcript") for k in keys) and any(k.endswith(":minutes") for k in keys)
    with page.expect_download() as event:
        minutes(page).locator("#fe-save").click()
    download = event.value
    assert "srcdoc" not in download.suggested_filename
    saved = original.with_name("saved.html")
    download.save_as(saved)
    assert meeting_file.parse_html(saved.read_text(encoding="utf-8")) == json.loads(payload)
    page.reload()
    expect(transcript(page).locator(".s").first).to_have_text("수정한 전사 문장")
    expect(minutes(page).locator("main h1")).to_have_text("수정한 회의록 제목")
    expect(minutes(page).locator(".s")).to_have_count(0)
    page.locator('[data-tab="transcript"]').click()
    transcript(page).locator(".outline-list .jump").nth(1).click()
    expect(transcript(page).locator("#pp")).to_have_class("pp on")
    # A different source file must not load the previous file's browser draft.
    another = original.with_name("another.html")
    shutil.copyfile(original, another)
    page.goto(another.as_uri())
    expect(transcript(page).locator(".s").first).not_to_have_text("수정한 전사 문장")
    page.goto(saved.as_uri())
    expect(transcript(page).locator(".s").first).to_have_text("수정한 전사 문장")
    expect(minutes(page).locator("main h1")).to_have_text("수정한 회의록 제목")
    page.locator('[data-tab="transcript"]').click()
    expect(transcript(page).locator("#annotation-outline")).to_have_count(1)
    transcript(page).locator('.outline-filter[data-kind="memo"]').click()
    expect(transcript(page).locator(".outline-list li:visible")).to_have_count(22)
    transcript(page).locator(".outline-list li:visible .jump").first.click()
    expect(transcript(page).locator("#pp")).to_have_class("pp on")


def test_outline_filter_follow_collapse_mobile_and_print(page, exported_meeting):
    open_export(page, exported_meeting)
    frame = transcript(page)
    for kind, count in [("memo", 22), ("comment", 22), ("all", 44)]:
        frame.locator(f'.outline-filter[data-kind="{kind}"]').click()
        expect(frame.locator(".outline-list li:visible")).to_have_count(count)
    frame.locator(".outline-list .jump").nth(6).click()
    expect(frame.locator('.outline-list [aria-current="location"]')).to_have_attribute("href", "#c-4")
    top = frame.locator("#annotation-outline").bounding_box()["y"]
    page.mouse.move(400, 300)
    frame.locator(".memo[data-ann]").last.evaluate("node => node.scrollIntoView({block:'start'})")
    expect(frame.locator('.outline-list [aria-current="location"]')).to_have_attribute("href", "#" + frame.locator(".memo[data-ann]").last.get_attribute("id"))
    assert abs(frame.locator("#annotation-outline").bounding_box()["y"] - top) < 1
    assert frame.locator('.outline-list [aria-current="location"]').evaluate("node => {const a=node.getBoundingClientRect(), b=node.closest('ol').getBoundingClientRect();return a.top>=b.top&&a.bottom<=b.bottom}")
    frame.locator(".outline-close").click()
    expect(frame.locator("#annotation-outline")).to_be_hidden()
    frame.locator(".outline-toggle").click()
    expect(frame.locator("#annotation-outline")).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(frame.locator("#annotation-outline")).to_be_hidden()
    frame.locator(".outline-toggle").click()
    frame.locator(".outline-list .jump").first.click()
    expect(frame.locator("#annotation-outline")).to_be_hidden()
    assert not frame.locator("html").evaluate("node => node.scrollWidth > innerWidth")
    page.emulate_media(media="print")
    expect(frame.locator("#annotation-outline")).to_be_hidden()
    expect(frame.locator(".outline-toggle")).to_be_hidden()


def test_no_recording_still_navigates_without_loading_outer_file(page, exported_meeting):
    exported_meeting["audio"].unlink()
    open_export(page, exported_meeting)
    frame = transcript(page)
    frame.locator(".outline-list .jump").nth(6).click()
    expect(frame.locator("#au")).to_have_count(0)
    assert frame.locator("#c-4").evaluate("node => node.getBoundingClientRect().top < 150")
    assert frame.locator("body").evaluate("location.href") == "about:srcdoc"


def test_missing_recording_has_mobile_safe_status_and_keeps_navigation(page, exported_meeting):
    page.set_viewport_size({"width": 390, "height": 844})
    open_export(page, exported_meeting, missing_audio=True)
    frame = transcript(page)
    expect(frame.locator("#pt")).to_contain_text("녹음 파일을 같은 폴더")
    expect(frame.locator("#pp")).to_be_disabled()
    assert not frame.locator("html").evaluate("node => node.scrollWidth > innerWidth")
    frame.locator(".outline-toggle").click()
    frame.locator(".outline-list .jump").nth(6).click()
    assert frame.locator("#c-4").evaluate("node => node.getBoundingClientRect().top < 150")


def test_storage_disabled_preserves_download_and_reports_the_failure(page, exported_meeting):
    page.add_init_script("""Object.defineProperty(window, 'showSaveFilePicker', {value: undefined});
        Storage.prototype.setItem = function () { throw new DOMException('Disabled', 'SecurityError'); };""")
    file = open_export(page, exported_meeting)
    frame = transcript(page)
    edit(frame, ".s", "임시저장 차단 상태에서 수정")
    expect(frame.locator("#fe-state")).to_contain_text("임시저장을 사용할 수 없어요")
    expect(frame.locator("#fe-save")).to_be_visible()
    with page.expect_download() as event:
        frame.locator("#fe-save").click()
    saved = file.with_name("denied-storage.html")
    event.value.save_as(saved)
    assert "임시저장 차단 상태에서 수정" in saved.read_text(encoding="utf-8")
    assert meeting_file.parse_html(saved.read_text(encoding="utf-8"))["format"] == meeting_file.FORMAT


def test_file_picker_and_autosave_keep_the_complete_document(page, exported_meeting):
    page.add_init_script("""Object.defineProperty(window, 'showSaveFilePicker', {value: async function(options) {
      window.savedName = options.suggestedName;
      return {name:'saved.html', createWritable:async () => ({write:async blob => {
        window.savedHtml = await blob.text(); window.writes = (window.writes || 0) + 1;
      }, close:async () => {}})};
    }});""")
    open_export(page, exported_meeting)
    frame = transcript(page)
    edit(frame, ".s", "첫 번째 파일 저장")
    frame.locator("#fe-save").click()
    page.wait_for_function("window.savedHtml && window.savedHtml.includes('첫 번째 파일 저장')")
    assert page.evaluate("window.savedName") == "meeting.html"
    assert 'id="f-minutes"' in page.evaluate("window.savedHtml")
    edit(frame, ".s", "후속 자동 저장")
    page.wait_for_function("window.writes >= 2 && window.savedHtml.includes('후속 자동 저장')")
    meeting_file.parse_html(page.evaluate("window.savedHtml"))


def test_autoplay_denied_is_caught_and_seek_survives_delayed_metadata(page, exported_meeting):
    page.add_init_script("HTMLMediaElement.prototype.play = function () { return Promise.reject(new DOMException('Blocked', 'NotAllowedError')); }")
    open_export(page, exported_meeting)
    frame = transcript(page)
    payload = json.loads(page.locator("#meeting-scribe-data").text_content())
    target = next(note for note in payload["annotations"] if note["body"] == "댓글 3 확인 사항")
    expected_time = json.loads(target["anchor"])["t0"]
    expect(frame.locator("#player")).to_be_visible()
    frame.locator("#au").evaluate("""node => {
      node.play = () => Promise.reject(new DOMException('Blocked', 'NotAllowedError'));
      Object.defineProperty(node, 'readyState', {value:0, configurable:true});
    }""")
    frame.locator(".outline-list .jump").nth(6).click()
    frame.locator("#au").evaluate("node => { delete node.readyState; node.dispatchEvent(new Event('loadedmetadata')); }")
    assert abs(audio_state(frame)["time"] - expected_time) < .001
    frame.locator("#pp").click()
    expect(frame.locator("#pp")).to_have_attribute("title", "재생 버튼을 눌러 주세요")


def test_m4a_archive_plays_in_browser(page, exported_meeting):
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("ffmpeg is not installed")
    audio = exported_meeting["audio"]
    subprocess.run([executable, "-loglevel", "error", "-nostdin", "-y", "-i", str(audio), "-c:a", "aac", str(audio.with_suffix(".m4a"))], check=True, capture_output=True)
    audio.unlink()
    open_export(page, exported_meeting)
    frame = transcript(page)
    frame.locator(".outline-list .jump").nth(6).click()
    expect(frame.locator("#pp")).to_have_class("pp on")
    assert abs(audio_state(frame)["time"] - 9.25) < 2
