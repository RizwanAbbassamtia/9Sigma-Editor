"""Caption look presets (docs/M5-CONTRACT.md section 3.4, ``config/captions.yaml``
``presets:``): the default ``bold-outline`` writes exactly the ASS of before, ``active-word``
colours the word being spoken (one event per word, touching), ``single-word-pop`` shows one
word at a time growing from 100 % to 112 % over 0.12 s, ``boxed`` puts the text on a
see-through box. Word times come from the timing (characters for Japanese); an edited cue
shares its time out by length. libass reads every preset."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sigma_editor.models.timeline import CaptionCue, CaptionStyle, Timeline
from sigma_editor.render.captions import (
    CaptionLook,
    build_ass,
    caption_events,
    caption_look,
    caption_looks,
    caption_style_for,
    chunk_cues,
    cue_tokens,
    cue_word_times,
    presets_json,
    write_ass,
)
from sigma_editor.render.fonts import fonts_dir
from sigma_editor.render.timeline_builder import parse_timing
from test_render_fixtures import make_render_tmp_fixture, requires_ffmpeg

render_tmp = make_render_tmp_fixture()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WORDS = "The diner opened at five".split()


def timing(words: list[str] = WORDS, start: float = 0.0, step: float = 0.4):
    return parse_timing({"duration_s": 3.0, "sentences": [{
        "id": "s1", "text": " ".join(words), "start_s": start,
        "end_s": start + step * len(words),
        "words": [{"text": w, "start_s": round(start + i * step, 3),
                   "end_s": round(start + i * step + step * 0.9, 3)}
                  for i, w in enumerate(words)],
    }]})


def dialogues(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Dialogue: ")]


def style_fields(text: str) -> list[str]:
    line = next(line for line in text.splitlines() if line.startswith("Style: "))
    return line[len("Style: "):].split(",")


def test_presets_load_with_their_defaults() -> None:
    looks = caption_looks()
    assert list(looks) == ["bold-outline", "active-word", "single-word-pop", "boxed"]
    assert looks["single-word-pop"].layout == "single_word"
    assert looks["single-word-pop"].pop_scale == pytest.approx(1.12)
    assert looks["single-word-pop"].pop_s == pytest.approx(0.12)
    assert looks["boxed"].box and 0 < looks["boxed"].box_opacity < 1
    assert looks["active-word"].active_colour == "#FFE34D"
    assert caption_look("Active Word").id == "active-word"
    assert caption_look("nonsense").id == "bold-outline"
    assert [p["id"] for p in presets_json()] == list(looks)


def test_the_style_fields_are_additive_and_normalised() -> None:
    old = CaptionStyle.model_validate({"font": "Noto Sans", "size": 48})
    assert old.preset == "bold-outline" and old.layout == "phrase"
    assert old.active_colour == "#FFE34D" and not old.single_words
    style = CaptionStyle.model_validate({"preset": "Single word pop", "layout": "single-word"})
    assert style.preset == "single-word-pop" and style.layout == "single_word"
    assert style.single_words
    assert CaptionStyle.model_validate({"preset": "x", "layout": "y"}).preset == "bold-outline"
    chosen = caption_style_for("English", "yellow", "active-word")
    assert chosen.preset == "active-word" and chosen.primary == "#FFE34D"
    assert caption_style_for("English").preset == "bold-outline"
    assert caption_style_for("English", None, "single-word-pop").layout == "single_word"


def test_bold_outline_writes_the_same_file_as_before() -> None:
    cues = [CaptionCue(start_s=0.0, end_s=1.6, text="The diner opened\\Nat five"),
            CaptionCue(start_s=1.7, end_s=3.3, text="Nobody noticed")]
    style = caption_style_for("English", "bold-white")
    text = build_ass(cues, style, "16:9", margin_v_px=60, shadow=0)
    assert style_fields(text)[15:18] == ["1", "2", "0"]  # outline, not a box
    assert dialogues(text) == [
        "Dialogue: 0,0:00:00.00,0:00:01.60,Default,,0,0,0,,{\\an2}The diner opened\\Nat five",
        "Dialogue: 0,0:00:01.70,0:00:03.30,Default,,0,0,0,,{\\an2}Nobody noticed",
    ]
    # The timing changes nothing for this look.
    assert build_ass(cues, style, "16:9", sentences=timing().sentences) == text


def test_active_word_colours_the_spoken_word_one_event_each() -> None:
    sentences = timing().sentences
    cues = chunk_cues(sentences, max_chars_per_line=12, max_lines=2)
    assert len(cues) == 1 and cues[0].text == "The diner\\Nopened at five"
    style = caption_style_for("English", "bold-white", "active-word")
    lines = dialogues(build_ass(cues, style, "16:9", sentences=sentences))
    assert len(lines) == 5
    assert lines[0] == (
        "Dialogue: 0,0:00:00.00,0:00:00.40,Default,,0,0,0,,"
        "{\\an2}{\\1c&H4DE3FF&}The{\\1c&HFFFFFF&} diner\\Nopened at five"
    )
    assert lines[2].endswith("{\\an2}The diner\\N{\\1c&H4DE3FF&}opened{\\1c&HFFFFFF&} at five")
    # Each word from its start to the next word's start, the last to the cue's end.
    times = [line.split(",")[1:3] for line in lines]
    assert times[0] == ["0:00:00.00", "0:00:00.40"] and times[-1][1] == "0:00:01.96"
    assert all(a[1] == b[0] for a, b in zip(times, times[1:], strict=False))  # touching
    custom = style.model_copy(update={"active_colour": "#22C55E"})
    assert "{\\1c&H5EC522&}diner" in build_ass(cues, custom, "16:9", sentences=sentences)


def test_single_word_pop_shows_one_word_at_a_time_with_the_pop() -> None:
    sentences = timing().sentences
    style = caption_style_for("English", "bold-white", "single-word-pop")
    cues = chunk_cues(sentences, single_word=style.single_words)
    assert [c.text for c in cues] == WORDS  # the builder makes one cue per word
    lines = dialogues(build_ass(cues, style, "16:9", sentences=sentences))
    assert len(lines) == 5
    assert lines[1] == (
        "Dialogue: 0,0:00:00.40,0:00:00.80,Default,,0,0,0,,"
        "{\\an2\\fscx100\\fscy100\\t(0,120,\\fscx112\\fscy112)}diner"
    )
    # Phrase cues are shown one word at a time too (a preset switched in the editor).
    phrase = chunk_cues(sentences, max_chars_per_line=42, max_lines=2)
    assert len(phrase) == 1
    words = dialogues(build_ass(phrase, style, "16:9", sentences=sentences))
    assert [line.rsplit("}", 1)[1] for line in words] == WORDS
    # The single_word layout with another look: single words, no pop.
    boxed_words = caption_style_for("English", "bold-white", "boxed").model_copy(
        update={"layout": "single_word"})
    plain = dialogues(build_ass(phrase, boxed_words, "16:9", sentences=sentences))
    assert len(plain) == 5 and "\\t(" not in plain[0] and plain[0].endswith("{\\an2}The")


def test_boxed_puts_the_text_on_a_see_through_box() -> None:
    cues = [CaptionCue(start_s=0.0, end_s=1.6, text="The diner\\Nopened")]
    style = caption_style_for("English", "bold-white", "boxed")
    text = build_ass(cues, style, "16:9")
    fields = style_fields(text)
    assert fields[15] == "3" and fields[16] == "12"  # opaque box, 12 px padding at 1080p
    alpha = round((1 - 0.55) * 255)
    assert fields[5] == fields[6] == f"&H{alpha:02X}000000"
    assert dialogues(text) == [
        "Dialogue: 0,0:00:00.00,0:00:01.60,Default,,0,0,0,,{\\an2}The diner\\Nopened"]


def test_word_times_fall_back_to_sharing_the_cue_out() -> None:
    sentences = timing().sentences
    edited = CaptionCue(start_s=0.0, end_s=2.0, text="A totally new line")  # 4 words, 5 timed
    assert cue_tokens(edited.text) == [("A", True), (" ", False), ("totally", True),
                                       (" ", False), ("new", True), (" ", False),
                                       ("line", True)]
    words = cue_word_times([edited], sentences)[0]
    assert [w.text for w in words] == ["A", "totally", "new", "line"]
    assert words[0].start_s == 0.0 and words[-1].end_s == pytest.approx(2.0)
    assert words[1].start_s == pytest.approx(2.0 * 1 / 15, abs=1e-3)  # by length: 1/15
    # Without any timing at all the cue's time is shared out too.
    alone = cue_word_times([edited], None)[0]
    assert [w.start_s for w in alone] == [w.start_s for w in words]
    events = caption_events(edited, words, caption_style_for("English", None, "active-word"))
    assert len(events) == 4 and events[0].active == 0 and events[-1].end_s == 2.0


def test_japanese_words_are_characters() -> None:
    sentences = parse_timing({"duration_s": 2.0, "sentences": [{
        "id": "j1", "text": "東京へ", "start_s": 0.0, "end_s": 1.5,
        "words": [{"text": "東京へ", "start_s": 0.0, "end_s": 1.5}]}]}).sentences
    cues = chunk_cues(sentences, max_chars_per_line=13, space_dependent=False)
    assert cues[0].text == "東京へ"
    words = cue_word_times(cues, sentences, space_dependent=False)[0]
    assert [w.text for w in words] == ["東", "京", "へ"]
    assert [w.start_s for w in words] == [0.0, 0.5, 1.0]
    style = caption_style_for("Japanese", None, "active-word")
    lines = dialogues(build_ass(cues, style, "16:9", sentences=sentences, space_dependent=False))
    assert len(lines) == 3 and lines[1].endswith("{\\an2}東{\\1c&H4DE3FF&}京{\\1c&HFFFFFF&}へ")


def test_a_custom_look_from_the_config_is_clamped() -> None:
    from sigma_editor.render.captions import _look_from

    look = _look_from("single-word-pop", {"pop_scale": 9, "pop_s": -1, "layout": "Phrase"},
                      CaptionLook("single-word-pop"))
    assert look.pop_scale == 2.0 and look.pop_s == 0.0 and look.layout == "phrase"
    assert not look.pops


def test_the_builder_uses_the_preset(render_tmp: Path) -> None:
    from sigma_editor.render.timeline_builder import (
        BuildRequest,
        build_timeline,
        resolve_scene_images,
    )
    from test_render_fixtures import write_project

    files = write_project(render_tmp, music=False, popups=False)
    request = BuildRequest(
        project_id="p", fmt="long", aspect="16:9", language="English", folder=files.folder,
        storyboard=files.storyboard, timing=parse_timing(files.timing),
        sources=resolve_scene_images(files.folder, files.storyboard), width=854, height=480,
        proxy=(854, 480), presets=[], caption_preset="single-word-pop",
    )
    timeline, _ = build_timeline(request)
    assert timeline.captions.style.preset == "single-word-pop"
    assert all(" " not in cue.text for cue in timeline.captions.cues)
    assert timeline.captions.cues[0].id == "cue-0001"
    again = Timeline.model_validate_json(timeline.model_dump_json())
    assert again.captions.style == timeline.captions.style


@requires_ffmpeg
@pytest.mark.parametrize("preset", ["bold-outline", "active-word", "single-word-pop", "boxed"])
def test_libass_burns_every_preset(render_tmp: Path, preset: str) -> None:
    sentences = timing().sentences
    style = caption_style_for("English", "bold-white", preset)
    cues = chunk_cues(sentences, max_chars_per_line=12, max_lines=2,
                      single_word=style.single_words)
    ass = write_ass(render_tmp / "captions.ass", cues, style, "16:9", sentences=sentences)
    fonts = str(fonts_dir()).replace("\\", "/").replace(":", "\\:")
    path = str(ass).replace("\\", "/").replace(":", "\\:")
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=0x203040:s=320x180:d=1:r=10", "-vf",
         f"subtitles=filename='{path}':fontsdir='{fonts}'", "-frames:v", "8", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, creationflags=NO_WINDOW,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    frames = len(completed.stdout) // (320 * 180 * 3)
    assert frames == 8
