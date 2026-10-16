"""The v0.2 render (docs/CONTRACT-v0.2.md section 6): a clip scene drawn from its video clip
(from ``in_s`` at ``speed``, the last frame held, cached under its own key), layers laid over
the scenes (a picture with a frame, an adjust and some see-through; a clip at its speed in a
small window), a sound track and the clips' own sound mixed under the voice, and a scene's
colour adjust, all in one real render of tiny media at 480p, checked frame by frame against
the source clip. Then the framed clip scene on its black canvas with the first frame held, the
render key and the media list with the new fields (a v0.1 timeline keeps its key), a range
render that cuts the layer and sound clips, and plain messages for missing files."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sigma_editor.edit.renderer import (
    render_content,
    render_key_for,
    timeline_media,
    timeline_range,
)
from sigma_editor.models.timeline import Timeline
from sigma_editor.render import assembly
from sigma_editor.render import ffmpeg as ff
from sigma_editor.render import scene_clips as sc
from sigma_editor.render.music import gain_key
from sigma_editor.render.presets import load_render_config
from test_render_fixtures import make_render_tmp_fixture, requires_ffmpeg, write_project

render_tmp = make_render_tmp_fixture()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOLD = {"preset": "hold", "start_rect": [0, 0, 1024, 576], "end_rect": [0, 0, 1024, 576]}
ZOOM_IN = {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
           "end_rect": [102.4, 57.6, 819.2, 460.8]}
SIZE = (854, 480)
CLIP = "01_media/walk.mp4"
TONE = "01_media/tone.wav"


# Media --------------------------------------------------------------------------------------


def write_clip(path: Path, seconds: float = 4.0, size: str = "320x180") -> Path:
    """A tiny test clip with a tone: the test pattern with its colours turning over time
    (90 degrees of hue a second), so every moment of the clip looks different."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-vf", "hue=h=90*t", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    return path


def write_tone(path: Path, seconds: float = 5.0) -> Path:
    """A stereo tone of ``seconds`` (a sound file for a sound track)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds}",
         "-ac", "2", str(path)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    return path


def media_for(render_tmp: Path) -> Any:
    """The mock edit (pictures, a silent 5 s voice) plus the clip and the tone in 01_media."""
    files = write_project(render_tmp, music=False, popups=False)
    write_clip(files.folder / CLIP)
    write_tone(files.folder / TONE)
    return files


def rgb_frame(path: Path, number: int, filters: str = "format=rgb24") -> np.ndarray:
    """Frame ``number`` of a video as an RGB array (ints), through ``filters``."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf",
         f"select=eq(n\\,{number}),{filters}", "-frames:v", "1", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, check=True, creationflags=NO_WINDOW,
    ).stdout
    frame = np.frombuffer(out, np.uint8)
    return frame.reshape(frame.size // (SIZE[0] * 3), SIZE[0], 3).astype(int)


def frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, creationflags=NO_WINDOW,
    ).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def colourfulness(frame: np.ndarray) -> float:
    """How far the colours are from grey (the spread of the channels, averaged)."""
    return float((frame.max(axis=2) - frame.min(axis=2)).mean())


# Timelines ------------------------------------------------------------------------------------


def v01_timeline() -> dict[str, Any]:
    """A timeline as v0.1 wrote it: two picture scenes (0-3 s, 3-5 s), a dissolve between
    them and a dip to black at the end, nothing of v0.2."""
    return {
        "project_id": "p-v02", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 5.0,
        "scenes": [
            {"index": 0, "image": "06_images/scene_01.png", "start_s": 0.0, "end_s": 3.0,
             "motion": ZOOM_IN, "transition_out": {"type": "film_dissolve", "duration_s": 0.4}},
            {"index": 1, "image": "06_images/scene_02.png", "start_s": 3.0, "end_s": 5.0,
             "motion": HOLD, "transition_out": {"type": "dip_black", "duration_s": 0.3}},
        ],
        "captions": {"enabled": False},
    }


def v02_timeline() -> Timeline:
    """The v0.1 timeline with everything of v0.2: scene 2 plays the clip from 0.5 s at 2x
    with its sound; a layer with a picture (1-4 s: cropped, flipped, two fifths of the frame,
    up and to the right, turned 15 degrees, a little see-through, brighter) and a layer with
    the clip in a small window bottom left (2-5 s, from 1 s at 1.5x); a sound track with the
    tone at -6 dB faded out (0.5-4 s); scene 1 half as saturated."""
    data = v01_timeline()
    data["scenes"][0]["adjust"] = {"saturation": -0.5}
    data["scenes"][1]["clip"] = {"path": CLIP, "in_s": 0.5, "speed": 2.0, "keep_sound": True,
                                 "duration_s": 4.0}
    data["layers"] = [
        {"name": "Badge", "clips": [{
            "source": "06_images/scene_03.png", "kind": "picture", "start_s": 1.0, "end_s": 4.0,
            "frame": {"x": 0.25, "y": -0.2, "scale": 0.4, "rotation": 15, "opacity": 0.8,
                      "crop": [0.1, 0, 0.1, 0], "flip_h": True},
            "adjust": {"brightness": 0.2},
        }]},
        {"clips": [{
            "source": CLIP, "kind": "clip", "start_s": 2.0, "end_s": 5.0, "in_s": 1.0,
            "speed": 1.5, "keep_sound": True, "frame": {"x": -0.2, "y": 0.2, "scale": 0.5},
        }]},
    ]
    data["audio_tracks"] = [{"clips": [{
        "source": TONE, "start_s": 0.5, "end_s": 4.0, "gain_db": -6, "fade_out_s": 0.5,
    }]}]
    return Timeline.model_validate(data)


def key_of(timeline: Timeline) -> str:
    return render_key_for(timeline, popups_enabled=True, x264_preset=None, media="same")


# The real render ------------------------------------------------------------------------------


@requires_ffmpeg
def test_clip_scene_layers_sounds_and_adjust_render_in_one_pass(render_tmp: Path) -> None:
    files = media_for(render_tmp)
    timeline = v02_timeline()
    config = load_render_config()
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    graph = plan.filter_graph

    # (b) The graph: the scene's eq for its frames (half a frame off each boundary), the two
    # layers laid over (the picture with its alpha kept through eq, turned, see-through; the
    # clip at 1.5x), the three extra sounds (the clip scene's at 2x, the layer clip's, the
    # tone at -6 dB faded out) summed and mixed under the voice before loudnorm.
    assert "[base]eq=brightness=0:contrast=1:saturation=0.5" in graph
    assert ":enable='between(t\\,-0.0167\\,2.9833)'[vadj]" in graph
    assert graph.count("overlay=") == 2 and "eof_action=pass" in graph
    assert "enable='between(t\\,1\\,4)'" in graph and "enable='between(t\\,2\\,5)'" in graph
    assert "crop=iw*0.8:ih*1:iw*0.1:ih*0,hflip,scale=342:192:force_original_aspect_ratio=" in graph
    assert "alphaextract" in graph and "alphamerge,rotate=0.2618:c=black@0" in graph
    assert "colorchannelmixer=aa=0.8" in graph and "format=yuva420p,setpts=PTS+1/TB[l0]" in graph
    assert "overlay=x='(W-w)/2+213.5':y='(H-h)/2-96'" in graph
    assert "setpts=(PTS-STARTPTS)/1.5,fps=30,tpad=stop_mode=clone:stop_duration=3[ls1]" in graph
    assert "atempo=1.5" in graph
    assert "atempo=2" in graph and "volume=-6dB" in graph and "afade=t=out:st=3:d=0.5" in graph
    assert "adelay=delays=24000S:all=1" in graph  # the tone at 0.5 s
    assert "[x0][x1][x2]amix=inputs=3:normalize=0:duration=longest[extra]" in graph
    assert "[mix][extra]amix=inputs=2:normalize=0:duration=first[mix2]" in graph
    assert "[mix2]loudnorm=I=-16:TP=-1.5:LRA=11" in graph
    # The inputs: the joined clips, the layer picture (looped), the layer clip (cut at the
    # input), the voice, then the three sounds (the scene's clip from 0.5 s for 2 x 2 s).
    assert len(plan.inputs) == 7
    assert plan.inputs[1][:5] == ["-loop", "1", "-framerate", "30", "-t"]
    assert plan.inputs[2][:4] == ["-ss", "1", "-t", "4.5"]
    assert plan.inputs[4] == ["-ss", "0.5", "-t", "4", "-i", str(files.folder / CLIP)]
    assert plan.inputs[6][:4] == ["-ss", "0", "-t", "3.5"]
    layout = plan.layout
    picture_job, clip_job = layout.scenes
    assert picture_job.renderer == "zoompan" and clip_job.renderer == "video"
    assert clip_job.request.clip is not None and clip_job.request.clip_file == files.folder / CLIP
    # Scene 2 is 60 frames plus the 6 of the dissolve before it; the dip to black (9 frames)
    # lies inside its own frames. Decoding starts 0.4 s of file before 0.5 s: at 0.1 s.
    assert (clip_job.request.lead_in, clip_job.request.tail_out, clip_job.frames) == (6, 0, 66)
    assert sc.clip_decode_start(clip_job.request.clip, 6, 30) == (0.1, 0.0)
    assert "tpad=stop_mode=clone:stop_duration=600,scale=854:480" in sc.video_clip_graph(clip_job)
    assert clip_job.hash_payload(clip_job.image_stat)["clip"]["in_s"] == 0.5

    # (a) The render finishes: video and sound, the planned length.
    result = ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                           log_path=files.edit_dir / "render.log")
    info = ff.probe_media(result.path)
    assert info.has_video and info.has_audio and (info.width, info.height) == SIZE
    assert abs(info.duration_s - plan.total_s) < 0.15 and plan.total_s == pytest.approx(5.0)
    assert result.scene_clips_total == 2 and result.scene_clips_reused == 0

    # (c) The clip scene's own clip holds exactly the planned frames, and shows the video:
    # its frame 51 (scene time 1.5 s, at 2x from 0.5 s) is the clip's frame at 3.5 s, not
    # the one at 2 s.
    scene_clip = files.edit_dir / "clips" / "proxy" / clip_job.file_name
    assert scene_clip.is_file() and frame_count(scene_clip) == 66
    cover = ("scale=854:480:force_original_aspect_ratio=increase:flags=lanczos,crop=854:480,"
             "format=rgb24")
    shown = rgb_frame(scene_clip, 51, f"{sc.TO_RGB},format=rgb24")
    right = rgb_frame(files.folder / CLIP, 105, cover)
    wrong = rgb_frame(files.folder / CLIP, 60, cover)
    assert diff(shown, right) < 12 and diff(shown, wrong) > 3 * diff(shown, right)
    # The same timeline plans the same key (the clip is reused); another speed is another clip.
    again = assembly.plan_layout(timeline, config.proxy, folder=files.folder, config=config)
    assert again.scenes[1].key == clip_job.key and again.scenes[0].key == picture_job.key
    faster = timeline.model_copy(deep=True)
    assert faster.scenes[1].clip is not None
    faster.scenes[1].clip.speed = 1.0
    moved = assembly.plan_layout(faster, config.proxy, folder=files.folder, config=config)
    assert moved.scenes[1].key != clip_job.key and moved.scenes[0].key == picture_job.key

    # The finished video, frame by frame. Proxy frame 135 (4.5 s) is the clip scene's frame
    # 51: the same picture top right (the badge is gone by then); bottom left the layer
    # clip's window still shows (its file ran out at 4 s, so it holds its last frame).
    first, body = layout.scene_frames[2]
    assert (first, body) == (84 + 12, 45)
    final = rgb_frame(result.path, 135, f"{sc.TO_RGB},format=rgb24")
    assert diff(final[20:200, 500:834], shown[20:200, 500:834]) < 10
    assert diff(final[230:450, 60:450], shown[230:450, 60:450]) > 25
    # Proxy frame 60 (2 s) is scene 1's frame 60 with half the colour (the adjust) and the
    # badge up and to the right (the layer picture); the top left corner holds the picture
    # alone (the layer clip's window, bottom left, starts at 2 s too).
    scene_frame = rgb_frame(files.edit_dir / "clips" / "proxy" / picture_job.file_name, 60,
                            f"{sc.TO_RGB},format=rgb24")
    final = rgb_frame(result.path, 60, f"{sc.TO_RGB},format=rgb24")
    corner, badge = (slice(0, 180), slice(0, 380)), (slice(60, 230), slice(520, 760))
    assert colourfulness(final[corner]) < 0.7 * colourfulness(scene_frame[corner])
    assert diff(final[badge], scene_frame[badge]) > 2 * diff(final[corner], scene_frame[corner])
    log = (files.edit_dir / "render.log").read_text(encoding="utf-8")
    assert "scene 02: drawn in" in log and "(video, 66 frames)" in log

    # A second render reuses both scene clips and the cut.
    second = ff.run_render(ff.build_plan(timeline, config.proxy, folder=files.folder,
                                         config=config),
                           files.edit_dir / "proxy2.mp4", work_dir=files.edit_dir,
                           log_path=files.edit_dir / "render.log")
    assert second.scene_clips_reused == 2 and second.cuts_reused == 2


@requires_ffmpeg
def test_a_framed_clip_scene_sits_on_a_black_canvas_and_holds_its_first_frame(
    render_tmp: Path,
) -> None:
    """A clip scene with a frame (half size, turned, moved right and down) is drawn on a
    black canvas; with ``in_s`` 0 the dissolve before the scene starts before the clip does,
    so its first frame is held for those frames."""
    files = media_for(render_tmp)
    data = v01_timeline()  # 3.5 s: one-pass loudnorm cannot take a silent voice under 3 s
    data["scenes"][0]["end_s"] = 1.0
    data["scenes"][1].update({"start_s": 1.0, "end_s": 3.5})
    data["scenes"][1]["clip"] = {"path": CLIP, "in_s": 0.0, "speed": 1.0}
    data["scenes"][1]["frame"] = {"x": 0.2, "y": 0.1, "scale": 0.5, "rotation": 10,
                                  "fit": "contain"}
    data["duration_s"] = 3.5
    timeline = Timeline.model_validate(data)
    config = load_render_config()
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    job = plan.layout.scenes[1]
    # The dissolve is capped at a quarter of the 1 s scene (8 frames): 4 before the boundary.
    assert (job.request.lead_in, job.frames) == (4, 79)
    graph = sc.video_clip_graph(job)
    assert graph.startswith("[0:v]setpts=(PTS-STARTPTS)/1,fps=30,tpad=start_mode=clone"
                            ":start_duration=0.1333:stop_mode=clone:stop_duration=600[src]")
    assert "scale=428:240:force_original_aspect_ratio=decrease" in graph
    assert "rotate=0.1745:c=black@0" in graph and "color=c=black:s=854x480:r=30[bg]" in graph
    assert "[bg][pic]overlay=x='(W-w)/2+170.8':y='(H-h)/2+48':format=rgb" in graph
    assert plan.filter_graph.count("overlay=") == 0 and "[mix]loudnorm" in plan.filter_graph
    result = ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                           log_path=files.edit_dir / "render.log")
    scene_clip = files.edit_dir / "clips" / "proxy" / job.file_name
    assert frame_count(scene_clip) == 79 and result.duration_s == pytest.approx(3.5, abs=0.1)
    # Frames 0 to 4 hold the clip's first frame (the lead-in before its start); frame 20
    # shows the clip 16 frames in. The corners stay black (the canvas), the window does not.
    held = rgb_frame(scene_clip, 0, f"{sc.TO_RGB},format=rgb24")
    assert diff(held, rgb_frame(scene_clip, 4, f"{sc.TO_RGB},format=rgb24")) < 3
    later = rgb_frame(scene_clip, 20, f"{sc.TO_RGB},format=rgb24")
    assert later[0:40, 0:200].max() <= 16 and later[440:480, 0:200].max() <= 16
    window = (slice(200, 280), slice(520, 680))
    assert later[window].mean() > 40 and diff(held[window], later[window]) > 5


# Keys, media and the range ---------------------------------------------------------------


def test_a_v01_timeline_keeps_its_render_content_and_key(render_tmp: Path) -> None:
    plain = Timeline.model_validate(v01_timeline())
    content = render_content(plain)
    assert "layers" not in content and "audio_tracks" not in content
    assert all(key not in scene for scene in content["scenes"]
               for key in ("clip", "frame", "adjust"))
    # The new fields spelled out with their defaults make the same content and key.
    spelled = v01_timeline()
    for scene in spelled["scenes"]:
        scene.update({"clip": None, "frame": None, "adjust": None})
    spelled.update({"layers": [], "audio_tracks": []})
    assert render_content(Timeline.model_validate(spelled)) == content
    assert key_of(Timeline.model_validate(spelled)) == key_of(plain)
    # The v0.2 fields count once set; ids, names and an AI voice's text do not.
    rich = v02_timeline()
    assert key_of(rich) != key_of(plain)
    content = render_content(rich)
    assert content["scenes"][0]["adjust"] == {"brightness": 0.0, "contrast": 0.0,
                                              "saturation": -0.5}
    assert content["scenes"][1]["clip"]["speed"] == 2.0 and "clip" not in content["scenes"][0]
    assert set(content["layers"][0]) == {"visible", "clips"}
    assert "id" not in content["layers"][0]["clips"][0]
    assert set(content["audio_tracks"][0]) == {"kind", "muted", "gain_db", "clips"}
    assert "text" not in content["audio_tracks"][0]["clips"][0]
    renamed = rich.model_copy(deep=True)
    renamed.layers[0].name = "Other"
    renamed.layers[0].clips[0].name = "The badge"
    renamed.audio_tracks[0].clips[0].text = "Spoken words"
    assert key_of(renamed) == key_of(rich)
    muted = rich.model_copy(deep=True)
    muted.audio_tracks[0].muted = True
    hidden = rich.model_copy(deep=True)
    hidden.layers[0].visible = False
    assert len({key_of(rich), key_of(muted), key_of(hidden)}) == 3
    # The media list grows by every clip, layer and sound source (each once).
    folder = render_tmp / "proj"
    assert timeline_media(folder, plain) == [
        folder / "06_images/scene_01.png", folder / "06_images/scene_02.png",
        folder / "05_voice/voice.wav",
    ]
    assert timeline_media(folder, rich)[3:] == [
        folder / CLIP, folder / "06_images/scene_03.png", folder / TONE,
    ]
    # The preview's loudness key hears the extra sounds too.
    config = load_render_config()
    assert gain_key(rich, folder, config, None) != gain_key(plain, folder, config, None)
    quieter = rich.model_copy(deep=True)
    quieter.audio_tracks[0].gain_db = -12
    assert gain_key(quieter, folder, config, None) != gain_key(rich, folder, config, None)


def test_extra_sounds_follow_the_scenes_and_the_tracks() -> None:
    config = load_render_config()
    sounds = ff.extra_sounds(v02_timeline(), Path("F:/x"), config)
    assert [s.what for s in sounds] == [
        "The clip walk.mp4 of scene 2", "The layer clip walk.mp4", "The sound file tone.wav",
    ]
    scene, layer, track = sounds
    assert (scene.start_s, scene.length_s, scene.in_s, scene.speed) == (3.0, 2.0, 0.5, 2.0)
    assert (layer.start_s, layer.length_s, layer.in_s, layer.speed) == (2.0, 3.0, 1.0, 1.5)
    assert (track.gain_db, track.fade_out_s, track.length_s) == (-6.0, 0.5, 3.5)
    assert scene.input_args()[:4] == ["-ss", "0.5", "-t", "4"]
    assert ff.atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"]
    assert ff.atempo_chain(0.3) == ["atempo=0.5", "atempo=0.6"]
    assert ff.atempo_chain(4) == ["atempo=4"] and ff.atempo_chain(1) == []
    chain = track.chain("[5:a]", "[x2]", rate=48000, total_s=5.0)
    assert chain == (
        "[5:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "asetpts=PTS-STARTPTS,volume=-6dB,afade=t=out:st=3:d=0.5,atrim=0:3.5,"
        "adelay=delays=24000S:all=1,apad=whole_dur=5,atrim=0:5[x2]"
    )
    # A clip without its sound kept, a muted track and a hidden layer add nothing.
    quiet = v02_timeline()
    assert quiet.scenes[1].clip is not None
    quiet.scenes[1].clip.keep_sound = False
    quiet.audio_tracks[0].muted = True
    quiet.layers[1].visible = False
    assert [s.what for s in ff.extra_sounds(quiet, Path("F:/x"), config)] == []
    assert ff.extra_sounds(Timeline.model_validate(v01_timeline()), Path("F:/x"), config) == []


def test_a_range_render_cuts_the_layer_and_sound_clips() -> None:
    rich = v02_timeline()
    part = timeline_range(rich, 2.0, 5.0)
    badge = part.layers[0].clips[0]  # 1-4 s at 1x: cut 1 s at its start
    assert (badge.start_s, badge.end_s, badge.in_s) == (0.0, 2.0, 1.0)
    assert badge.id == rich.layers[0].clips[0].id and part.layers[0].id == rich.layers[0].id
    window = part.layers[1].clips[0]  # 2-5 s: starts with the range, nothing cut
    assert (window.start_s, window.end_s, window.in_s) == (0.0, 3.0, 1.0)
    tone = part.audio_tracks[0].clips[0]  # 0.5-4 s: cut 1.5 s
    assert (tone.start_s, tone.end_s, tone.in_s) == (0.0, 2.0, 1.5)
    assert tone.id == rich.audio_tracks[0].clips[0].id and tone.fade_out_s == 0.5
    assert part.scenes[1].clip is not None and part.scenes[1].clip.in_s == 0.5
    cut_scene = timeline_range(rich, 4.0, 5.0).scenes[0]  # the clip scene cut 1 s in, at 2x
    assert cut_scene.clip is not None and cut_scene.clip.in_s == pytest.approx(0.5 + 1.0 * 2.0)
    assert (cut_scene.start_s, cut_scene.end_s) == (0.0, 1.0)
    later = timeline_range(rich, 3.0, 5.0)
    assert later.layers[1].clips[0].in_s == pytest.approx(1.0 + 1.0 * 1.5)  # at 1.5x
    assert later.layers[0].clips[0].end_s == 1.0 and later.audio_tracks[0].clips[0].end_s == 1.0
    # A clip outside the range goes; its layer or track stays, empty.
    tail = timeline_range(rich, 4.2, 5.0)
    assert tail.layers[0].clips == [] and tail.audio_tracks[0].clips == []
    assert [c.start_s for c in tail.layers[1].clips] == [0.0]
    assert tail.layers[1].clips[0].in_s == pytest.approx(1.0 + 2.2 * 1.5)
    assert len(tail.layers) == 2 and len(tail.audio_tracks) == 1


@requires_ffmpeg
def test_picture_scene_jobs_hash_as_before_and_clip_jobs_add_their_clip(
    render_tmp: Path,
) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    write_clip(files.folder / CLIP)
    config = load_render_config()
    layout = assembly.plan_layout(v02_timeline(), config.proxy, folder=files.folder,
                                  config=config)
    picture, clip = layout.scenes
    assert set(picture.hash_payload(picture.image_stat)) == {
        "engine", "image", "image_mtime_ns", "image_bytes", "motion", "frames", "keyframes",
        "size", "canvas", "fps", "seed", "motion_settings", "scale_flags", "encoding",
        "renderer",
    }
    payload = clip.hash_payload(clip.image_stat)
    assert payload["renderer"] == "video" and payload["motion"] is None
    assert payload["clip"]["file"] == str(files.folder / CLIP) and payload["clip"]["bytes"] > 0
    assert payload["clip"]["lead_in"] == 6 and payload["clip"]["frame"] is None
    assert clip.provider is None and clip.clip_stat is not None


@requires_ffmpeg
def test_missing_files_read_plainly(render_tmp: Path) -> None:
    files = media_for(render_tmp)
    config = load_render_config()

    def plan(timeline: Timeline) -> ff.RenderPlan:
        return ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)

    no_badge = v02_timeline()
    no_badge.layers[0].clips[0].source = "01_media/badge.png"
    with pytest.raises(ff.RenderError, match=r"The layer picture badge\.png is missing: .*badge"):
        plan(no_badge)
    no_window = v02_timeline()
    no_window.layers[1].clips[0].source = "01_media/gone.mp4"
    with pytest.raises(ff.RenderError, match=r"The layer clip gone\.mp4 is missing"):
        plan(no_window)
    no_tone = v02_timeline()
    no_tone.audio_tracks[0].clips[0].source = "01_media/rain.mp3"
    with pytest.raises(ff.RenderError, match=r"The sound file rain\.mp3 is missing"):
        plan(no_tone)
    no_clip = v02_timeline()
    assert no_clip.scenes[1].clip is not None
    no_clip.scenes[1].clip.path = "01_media/lost.mp4"
    with pytest.raises(ff.RenderError, match=r"The clip for scene 2 is missing"):
        plan(no_clip)
    # A clip FFmpeg cannot read stops the render with the clip's name and the reason (its
    # sound is not kept here, so nothing probes the file before the scene is drawn).
    broken = v02_timeline()
    assert broken.scenes[1].clip is not None
    broken.scenes[1].clip.keep_sound = False
    broken.layers = broken.layers[:1]
    (files.folder / CLIP).write_bytes(b"not a video" * 100)
    with pytest.raises(ff.RenderError, match=r"The clip walk\.mp4 for scene 2 could not be read: "):
        ff.run_render(plan(broken), files.edit_dir / "bad.mp4", work_dir=files.edit_dir,
                      log_path=files.edit_dir / "render.log")
