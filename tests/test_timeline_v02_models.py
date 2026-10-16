"""The v0.2 timeline fields (docs/CONTRACT-v0.2.md sections 3 and 4): defaults that leave a
v0.1 timeline untouched, ids for layers and clips, overlaps refused, the uses of a file."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sigma_editor.models.timeline import ColourAdjust, FrameAdjust, Timeline


def base_timeline() -> dict:
    return {
        "project_id": "p1", "format": "long", "aspect": "16:9", "width": 1920, "height": 1080,
        "duration_s": 10.0,
        "scenes": [
            {"index": 0, "image": "06_images/scene_01.png", "start_s": 0.0, "end_s": 5.0,
             "motion": {"preset": "hold", "start_rect": [0, 0, 1920, 1080],
                        "end_rect": [0, 0, 1920, 1080]}},
            {"index": 1, "image": "06_images/scene_02.jpg", "start_s": 5.0, "end_s": 10.0,
             "motion": {"preset": "hold", "start_rect": [0, 0, 1920, 1080],
                        "end_rect": [0, 0, 1920, 1080]},
             "clip": {"path": "01_media/walk.mp4", "speed": 2.0, "keep_sound": True}},
        ],
    }


def test_a_v01_timeline_reads_with_empty_defaults() -> None:
    data = base_timeline()
    del data["scenes"][1]["clip"]
    timeline = Timeline.model_validate(data)
    assert timeline.layers == [] and timeline.audio_tracks == []
    assert all(s.clip is None and s.frame is None and s.adjust is None for s in timeline.scenes)
    assert timeline.source_files == []


def test_layers_tracks_and_ids() -> None:
    data = base_timeline()
    data["layers"] = [
        {"clips": [
            {"source": "01_media/logo.png", "start_s": 1.0, "end_s": 3.0,
             "frame": {"scale": 0.4, "x": 0.25, "y": -0.25, "crop": [0.1, 0, 0, 0]}},
            {"source": "01_media/b.mp4", "kind": "clip", "start_s": 3.0, "end_s": 6.0,
             "speed": 1.5, "in_s": 2.0, "adjust": {"saturation": 0.5}},
        ]},
        {"name": "Titles", "visible": False},
    ]
    data["audio_tracks"] = [
        {"clips": [{"source": "01_media/voice/intro.mp3", "start_s": 0.0, "end_s": 4.0,
                    "text": "Welcome", "voice": "Rachel", "gain_db": -3, "fade_out_s": 0.5}]},
    ]
    timeline = Timeline.model_validate(data)
    assert [layer.id for layer in timeline.layers] == ["layer-01", "layer-02"]
    assert [layer.name for layer in timeline.layers] == ["Layer 1", "Titles"]
    assert [clip.id for clip in timeline.layers[0].clips] == ["lc-001", "lc-002"]
    assert timeline.layers[0].clips[0].frame.crop == [0.1, 0.0, 0.0, 0.0]
    assert timeline.layers[0].clips[1].speed == 1.5 and timeline.layers[0].clips[1].in_s == 2.0
    assert timeline.audio_tracks[0].id == "track-01" and timeline.audio_tracks[0].name == "Sound 1"
    assert timeline.audio_tracks[0].clips[0].id == "ac-001"
    assert timeline.scenes[1].clip is not None and timeline.scenes[1].clip.speed == 2.0
    assert timeline.source_files == [
        "01_media/walk.mp4", "01_media/logo.png", "01_media/b.mp4", "01_media/voice/intro.mp3",
    ]
    assert timeline.uses_of("01_media/walk.mp4") == ["scene 2"]
    assert timeline.uses_of("01_media/logo.png") == ["Layer 1"]
    assert timeline.uses_of("01_media/voice/intro.mp3") == ["Sound 1"]
    assert timeline.uses_of("01_media/nothing.png") == []
    # a second read keeps the ids
    again = Timeline.model_validate(timeline.model_dump(mode="json"))
    assert [c.id for c in again.layers[0].clips] == ["lc-001", "lc-002"]


def test_overlapping_clips_are_refused() -> None:
    data = base_timeline()
    data["layers"] = [{"clips": [
        {"source": "a.png", "start_s": 1.0, "end_s": 3.0},
        {"source": "b.png", "start_s": 2.5, "end_s": 4.0},
    ]}]
    with pytest.raises(ValidationError, match="overlap"):
        Timeline.model_validate(data)
    data = base_timeline()
    data["audio_tracks"] = [{"clips": [
        {"source": "a.mp3", "start_s": 0.0, "end_s": 2.0},
        {"source": "b.mp3", "start_s": 1.0, "end_s": 2.0},
    ]}]
    with pytest.raises(ValidationError, match="overlap"):
        Timeline.model_validate(data)
    # clips out of order are sorted, not refused
    data = base_timeline()
    data["layers"] = [{"clips": [
        {"source": "b.png", "start_s": 4.0, "end_s": 5.0},
        {"source": "a.png", "start_s": 1.0, "end_s": 3.0},
    ]}]
    assert [c.source for c in Timeline.model_validate(data).layers[0].clips] == ["a.png", "b.png"]


def test_frame_and_adjust_limits() -> None:
    assert FrameAdjust().is_default and FrameAdjust().is_identity
    assert not FrameAdjust(fit="cover").is_default and FrameAdjust(fit="cover").is_identity
    assert not FrameAdjust(scale=0.5).is_identity
    assert FrameAdjust(crop=[2, 0, 0, 0]).crop[0] == 0.95  # clamped
    with pytest.raises(ValidationError):
        FrameAdjust(crop=[0.6, 0, 0.6, 0])  # nothing left
    with pytest.raises(ValidationError):
        FrameAdjust(opacity=1.5)
    assert ColourAdjust().is_default and not ColourAdjust(contrast=0.2).is_default
    with pytest.raises(ValidationError):
        ColourAdjust(brightness=2)
    data = base_timeline()
    data["scenes"][1]["clip"]["speed"] = 9
    with pytest.raises(ValidationError):
        Timeline.model_validate(data)
