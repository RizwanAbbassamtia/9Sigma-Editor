"""Edit stage data: ``07_edit/timeline.json`` (:class:`Timeline`), the approval edits and the
review payload. See docs/M3-M4-CONTRACT.md section 3.

``timeline.json`` is the source of truth for the video: the M5 editor edits this file and
the renderer (``render/ffmpeg.py``) reads nothing else. Every time is a second on the voice
clock (``05_voice/timing.json``); every rectangle is ``[x, y, w, h]`` in pixels of the source
image and has the frame's aspect ratio, so the renderer only has to interpolate and zoom.

The front end mirrors these in ``frontend/src/types/timeline.ts``.

M5.1 (docs/M5-CONTRACT.md section 1) adds, all optional with defaults so an older
``timeline.json`` still reads, plays and renders the same: ``revision``, ``markers`` and
``render.preview_gain_db`` (the renderer ignores them), ``scenes[].sentence_ids`` and
``edited``, ``motion.ease`` (overrides the preset's curve), ``voice.segments`` (the voice
track as sentence clips), popup ``id`` / ``anchor`` / ``edited`` / ``locked``, cue ``id`` /
``edited``, the caption ``preset`` / ``active_colour`` / ``layout`` and the music ``stem`` and
``envelope`` (``07_edit/music_ducked.wav`` and ``music_envelope.json``). Missing popup, cue
and marker ids are filled with positional ones (``pop-001``, ``cue-0001``, ``mark-001``) when
a timeline is read. The route shapes of the live preview (``api/preview.py``) are at the end.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .transitions import DEFAULT_MOTION_STYLE, MotionStyle, clamp_transition_s

Aspect = Literal["16:9", "9:16"]
PopupAnim = Literal["slide_left", "fade", "pop"]
RenderStatus = Literal["not_rendered", "queued", "rendering", "done", "failed"]
MotionEase = Literal[
    "ease_in_out_cubic", "ease_out_quart", "ease_in_out_sine", "snap", "linear"
]
CaptionPreset = Literal["bold-outline", "active-word", "single-word-pop", "boxed"]
CaptionLayout = Literal["phrase", "single_word"]

DEFAULT_PRESETS: tuple[str, ...] = ("1080p",)
PROXY_PRESET = "proxy"
TIMELINE_FILE = "timeline.json"
MOTION_EASES: tuple[str, ...] = (
    "ease_in_out_cubic", "ease_out_quart", "ease_in_out_sine", "snap", "linear",
)
"""The curves ``motion.ease`` may name (``docs/design/MOTION-PARITY.md`` section 2)."""
CAPTION_PRESETS: tuple[str, ...] = ("bold-outline", "active-word", "single-word-pop", "boxed")
DEFAULT_CAPTION_PRESET = "bold-outline"
CAPTION_LAYOUTS: tuple[str, ...] = ("phrase", "single_word")
DEFAULT_ACTIVE_COLOUR = "#FFE34D"
DEFAULT_MARKER_COLOUR = "#60A5FA"


def _key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_motion_ease(value: Any) -> str | None:
    """``motion.ease``: one of :data:`MOTION_EASES` (``"Ease-Out Quart"`` reads as
    ``ease_out_quart``); empty or unknown -> ``None`` (the preset's own curve)."""
    key = _key(value)
    return key if key in MOTION_EASES else None


def normalize_caption_preset(value: Any) -> str:
    """``captions.style.preset``: ``"Active word"`` -> ``active-word``; empty or unknown ->
    :data:`DEFAULT_CAPTION_PRESET`."""
    key = _key(value).replace("_", "-")
    return key if key in CAPTION_PRESETS else DEFAULT_CAPTION_PRESET


def normalize_caption_layout(value: Any) -> str:
    """``captions.style.layout``: ``"single-word"`` -> ``single_word``; anything else ->
    ``phrase``."""
    key = _key(value)
    return key if key in CAPTION_LAYOUTS else "phrase"


def _check_rect(value: Any) -> list[float]:
    if not isinstance(value, list | tuple) or len(value) != 4:
        raise ValueError("a rectangle is [x, y, w, h]")
    rect = [float(v) for v in value]
    if rect[2] <= 0 or rect[3] <= 0:
        raise ValueError("a rectangle needs a positive width and height")
    return rect


class VoiceSegment(BaseModel):
    """One sentence clip of the voice track: ``src_start_s`` .. ``src_end_s`` of the voice
    file (``voice.path``) plays at ``at_s`` on the timeline."""

    sentence_id: str = Field(min_length=1)
    src_start_s: float = Field(ge=0, description="where the clip starts in the voice file")
    src_end_s: float = Field(ge=0, description="where it ends in the voice file")
    at_s: float = Field(ge=0, description="where it plays on the timeline")

    @model_validator(mode="after")
    def _ordered(self) -> VoiceSegment:
        if self.src_end_s <= self.src_start_s:
            raise ValueError(
                f"the voice clip of sentence {self.sentence_id} ends ({self.src_end_s}) before "
                f"it starts ({self.src_start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.src_end_s - self.src_start_s, 6)

    @property
    def end_s(self) -> float:
        """Where the clip stops on the timeline."""
        return round(self.at_s + self.duration_s, 6)


class VoiceTrack(BaseModel):
    path: str = "05_voice/voice.wav"
    """Relative to the project folder (or absolute)."""
    start_s: float = Field(default=0.0, ge=0)
    segments: list[VoiceSegment] | None = Field(
        default=None,
        description="the voice as sentence clips placed on the timeline (cut from the voice "
        "file, silence in between); null = the whole voice file from start_s",
    )


class MusicTrack(BaseModel):
    path: str | None = None
    """Absolute path of the track in the channel's music folder; ``None`` = no music."""
    start_s: float = Field(default=0.0, ge=0, description="offset into the track")
    gain_db: float = -18.0
    duck_db: float = -12.0
    duck_attack_s: float = Field(default=0.3, ge=0)
    duck_release_s: float = Field(default=0.8, ge=0)
    fade_in_s: float = Field(default=1.0, ge=0)
    fade_out_s: float = Field(default=2.0, ge=0)
    license_ok: bool = False
    stem: str | None = Field(
        default=None,
        description="07_edit/music_ducked.wav: the music as it sounds in the video (offset, "
        "looped, volume, fades and the dip under the voice), made by the edit step",
    )
    envelope: str | None = Field(
        default=None,
        description="07_edit/music_envelope.json: the dip under the voice, {step_s, gain_db}",
    )


# Motion v2 (docs/TRANSITIONS-V2-CONTRACT.md section 2): how the camera moves over a picture.
# ``ken_burns`` pans and zooms the flat picture (eased); ``parallax`` adds 2.5D depth from the
# depth model and falls back to ``ken_burns`` (saying so in the edit review) without it.
DEFAULT_PARALLAX_STRENGTH = 0.035
_MOTION_STYLE_ALIASES: dict[str, MotionStyle] = {
    "ken_burns": "ken_burns",
    "kenburns": "ken_burns",
    "kb": "ken_burns",
    "pan_and_zoom": "ken_burns",
    "flat": "ken_burns",
    "parallax": "parallax",
    "2.5d": "parallax",
    "2_5d": "parallax",
    "2.5d_parallax": "parallax",
    "depth": "parallax",
}


def normalize_motion_style(value: Any) -> MotionStyle:
    """``"Ken Burns"``, ``"kenburns"`` -> ``ken_burns``; ``"2.5D parallax"`` -> ``parallax``;
    empty or unknown -> :data:`DEFAULT_MOTION_STYLE`."""
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _MOTION_STYLE_ALIASES.get(key, DEFAULT_MOTION_STYLE)


class TimelineMotion(BaseModel):
    preset: str = "hold"
    start_rect: list[float] = Field(description="[x, y, w, h] in source-image pixels")
    end_rect: list[float] = Field(description="[x, y, w, h] in source-image pixels")
    style: MotionStyle = Field(
        default=DEFAULT_MOTION_STYLE,
        description="ken_burns (eased pan and zoom) or parallax (2.5D depth; needs the depth "
        "model, else rendered as ken_burns)",
    )
    strength: float = Field(
        default=DEFAULT_PARALLAX_STRENGTH,
        ge=0.0,
        le=0.2,
        description="parallax only: share of the frame width the nearest things move against "
        "the farthest during a typical pan",
    )
    ease: MotionEase | None = Field(
        default=None,
        description="the move's speed curve when it should differ from the preset's own: "
        "ease_in_out_cubic, ease_out_quart, ease_in_out_sine, snap or linear; null = the "
        "preset's curve",
    )

    @field_validator("start_rect", "end_rect", mode="before")
    @classmethod
    def _rects(cls, value: Any) -> list[float]:
        return _check_rect(value)

    @field_validator("style", mode="before")
    @classmethod
    def _style(cls, value: Any) -> MotionStyle:
        return normalize_motion_style(value)

    @field_validator("ease", mode="before")
    @classmethod
    def _ease(cls, value: Any) -> str | None:
        return normalize_motion_ease(value)


# Transitions v2 (docs/TRANSITIONS-V2-CONTRACT.md section 1): the ids the transition engine
# (render/transitions/) can draw. Older timeline.json files carry FFmpeg xfade names; they are
# mapped to their v2 look when the file is read, and anything unknown becomes a film dissolve.
TRANSITION_IDS: tuple[str, ...] = (
    "film_dissolve",
    "dip_black",
    "dip_white",
    "blur_dissolve",
    "zoom_through",
    "push_left",
    "push_right",
    "push_up",
    "push_down",
    "whip_left",
    "whip_right",
    "whip_up",
    "whip_down",
    "luma_wipe_left",
    "luma_wipe_right",
    "luma_wipe_up",
    "luma_wipe_down",
    "luma_wipe_diag",
    "crosswarp",
    "directional_warp",
    "light_leak_warm",
    "film_burn",
    "glitch_subtle",
    "spin_zoom",
    "morph_blend",
    "cube_left",
    "cube_right",
)
DEFAULT_TRANSITION = "film_dissolve"
LEGACY_TRANSITIONS: dict[str, str] = {
    "fade": "film_dissolve",
    "wipeleft": "luma_wipe_left",
    "slideleft": "push_left",
    "zoomin": "zoom_through",
    "fadeblack": "dip_black",
    "fadewhite": "dip_white",
}


def _catalogue_legacy(name: str) -> str | None:
    """The ``legacy`` table of ``config/transitions.yaml`` (wiperight, slideup ...), read
    lazily so this model stays importable without the config folder."""
    try:
        from ..config_files import transition_catalog

        return transition_catalog().resolve(name)
    except Exception:  # noqa: BLE001 - the contract's own table below still applies
        return None


def normalize_transition_id(value: Any) -> str:
    """A v2 transition id for ``value``: v2 ids pass through, old xfade names map to their
    v2 look (the contract's table, extended by the catalogue's ``legacy`` table), anything
    else becomes :data:`DEFAULT_TRANSITION`."""
    text = str(value or "").strip()
    if text in TRANSITION_IDS:
        return text
    key = text.lower().replace("-", "_").replace(" ", "_")
    if key in TRANSITION_IDS:
        return key
    if key in LEGACY_TRANSITIONS:
        return LEGACY_TRANSITIONS[key]
    resolved = _catalogue_legacy(text) if text else None
    return resolved if resolved in TRANSITION_IDS else DEFAULT_TRANSITION


class TimelineTransition(BaseModel):
    type: str = DEFAULT_TRANSITION
    """A transition id of the v2 engine (render/transitions/); old xfade names are mapped."""
    duration_s: float = Field(default=0.6, ge=0)
    """Seconds; anything above ``MAX_TRANSITION_S`` (3 s) is brought down to it."""

    @field_validator("type", mode="before")
    @classmethod
    def _v2_type(cls, value: Any) -> str:
        return normalize_transition_id(value)

    @field_validator("duration_s")
    @classmethod
    def _at_most_max(cls, value: float) -> float:
        return clamp_transition_s(value)


class TimelineScene(BaseModel):
    index: int = Field(ge=0)
    image: str = Field(description="relative to the project folder, e.g. 06_images/scene_01.png")
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    motion: TimelineMotion
    transition_out: TimelineTransition = TimelineTransition()
    locked: bool = False
    sentence_ids: list[str] = Field(
        default=[], description="the sentences of timing.json this scene shows"
    )
    edited: list[str] = Field(
        default=[],
        description="the fields a person changed (motion, transition_out, image, times ...); "
        "a rebuild keeps them",
    )

    @model_validator(mode="after")
    def _ordered(self) -> TimelineScene:
        if self.end_s <= self.start_s:
            raise ValueError(
                f"scene {self.index + 1} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


class PopupAnchor(BaseModel):
    """A popup tied to a sentence: it starts ``offset_s`` after that sentence starts."""

    sentence_id: str = Field(min_length=1)
    offset_s: float = 0.0


class TimelinePopup(BaseModel):
    scene: int = Field(ge=0, description="the scene index this popup belongs to")
    text: str = Field(min_length=1)
    style: str = ""
    position: str = "bottom-left"
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    anim: PopupAnim = "slide_left"
    png: str = Field(default="", description="the rendered PNG, relative to the project folder")
    id: str = Field(default="", description="stable id (pop-001 ...); filled when empty")
    anchor: PopupAnchor | None = None
    edited: bool = False
    locked: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> TimelinePopup:
        if self.end_s < self.start_s:
            raise ValueError(f"the popup {self.text!r} ends before it starts")
        return self


class CaptionStyle(BaseModel):
    font: str = "Noto Sans"
    size: int = Field(default=48, ge=8, description="pixels at 1080p; scaled with the frame")
    primary: str = "#FFFFFF"
    outline: int = Field(default=2, ge=0)
    position: str = "bottom"
    max_lines: int = Field(default=2, ge=1)
    max_chars_per_line: int = Field(default=42, ge=4)
    rtl: bool = False
    # Additive to the contract shape: what config/captions.yaml's named styles carry.
    bold: bool = True
    outline_colour: str = "#000000"
    # M5.1: the look presets of config/captions.yaml (render/captions.py writes them).
    preset: CaptionPreset = Field(
        default="bold-outline",
        description="bold-outline (the classic), active-word (the spoken word changes colour), "
        "single-word-pop (one word at a time with a small pop) or boxed (text on a "
        "see-through box)",
    )
    active_colour: str = Field(
        default=DEFAULT_ACTIVE_COLOUR, description="#RRGGBB of the spoken word (active-word)"
    )
    layout: CaptionLayout = Field(
        default="phrase",
        description="phrase (a line or two at a time) or single_word (one word at a time; "
        "single-word-pop always shows single words)",
    )

    @field_validator("preset", mode="before")
    @classmethod
    def _preset(cls, value: Any) -> str:
        return normalize_caption_preset(value)

    @field_validator("layout", mode="before")
    @classmethod
    def _layout(cls, value: Any) -> str:
        return normalize_caption_layout(value)

    @property
    def single_words(self) -> bool:
        """Whether captions show one word at a time (the layout, or single-word-pop)."""
        return self.layout == "single_word" or self.preset == "single-word-pop"


class CaptionCue(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    text: str
    id: str = Field(default="", description="stable id (cue-0001 ...); filled when empty")
    edited: bool = False


class TimelineCaptions(BaseModel):
    enabled: bool = True
    style: CaptionStyle = CaptionStyle()
    cues: list[CaptionCue] = []


class TimelineClip(BaseModel):
    """An intro or outro video file; ``None`` = none."""

    path: str | None = None


class ProxySize(BaseModel):
    width: int = Field(default=854, gt=0)
    height: int = Field(default=480, gt=0)


class TimelineMarker(BaseModel):
    """A marker on the timeline (the renderer ignores them; chapters feed the export)."""

    id: str = Field(default="", description="stable id (mark-001 ...); filled when empty")
    t: float = Field(ge=0, description="seconds on the timeline")
    label: str = ""
    colour: str = DEFAULT_MARKER_COLOUR
    chapter: bool = False


class TimelineRender(BaseModel):
    """What the live preview needs to sound like the finished file (the renderer ignores it)."""

    preview_gain_db: float | None = Field(
        default=None,
        description="decibels the preview adds to the voice + music mix so it plays as loud "
        "as the finished video (measured by the edit step, EBU R128)",
    )


def _fill_ids(items: list[Any], prefix: str, width: int) -> None:
    """Give every item of ``items`` (objects with an ``id``) a unique id: empty or repeated
    ids become ``<prefix>-<n>`` with the item's position, skipping ids already in use."""
    used = {str(item.id) for item in items if item.id}
    seen: set[str] = set()
    for position, item in enumerate(items, start=1):
        current = str(item.id or "")
        if current and current not in seen:
            seen.add(current)
            continue
        number = position
        candidate = f"{prefix}-{number:0{width}d}"
        while candidate in used or candidate in seen:
            number += 1
            candidate = f"{prefix}-{number:0{width}d}"
        item.id = candidate
        used.add(candidate)
        seen.add(candidate)


class Timeline(BaseModel):
    """``07_edit/timeline.json``."""

    version: int = 1
    project_id: str
    format: Literal["long", "shorts"]
    aspect: Aspect
    fps: int = Field(default=30, ge=1, le=120)
    width: int = Field(gt=0, description="frame size of the first requested preset")
    height: int = Field(gt=0)
    duration_s: float = Field(ge=0, description="end of the last scene")
    voice: VoiceTrack = VoiceTrack()
    music: MusicTrack = MusicTrack()
    scenes: list[TimelineScene]
    popups: list[TimelinePopup] = []
    captions: TimelineCaptions = TimelineCaptions()
    intro: TimelineClip = TimelineClip()
    outro: TimelineClip = TimelineClip()
    presets: list[str] = list(DEFAULT_PRESETS)
    proxy: ProxySize = ProxySize()
    revision: int = Field(
        default=1, ge=1, description="counts saves of the timeline (the renderer ignores it)"
    )
    markers: list[TimelineMarker] = []
    render: TimelineRender = TimelineRender()

    @model_validator(mode="after")
    def _ids(self) -> Timeline:
        _fill_ids(self.popups, "pop", 3)
        _fill_ids(self.captions.cues, "cue", 4)
        _fill_ids(self.markers, "mark", 3)
        return self

    @model_validator(mode="after")
    def _scenes_in_order(self) -> Timeline:
        previous_end = 0.0
        for position, scene in enumerate(self.scenes):
            if scene.index != position:
                raise ValueError(
                    f"scene {position + 1} carries index {scene.index}; scenes must be numbered "
                    "0, 1, 2... in order"
                )
            if scene.start_s + 1e-6 < previous_end:
                raise ValueError(
                    f"scene {position + 1} starts at {scene.start_s} s, before the previous "
                    f"scene ends ({previous_end} s)"
                )
            previous_end = scene.end_s
        if self.scenes and self.duration_s + 1e-6 < previous_end:
            raise ValueError(
                f"duration_s ({self.duration_s}) is shorter than the last scene's end "
                f"({previous_end})"
            )
        return self

    @property
    def total_s(self) -> float:
        return round(self.scenes[-1].end_s, 3) if self.scenes else 0.0

    @property
    def transition_types(self) -> list[str]:
        """Transition types in order of first use."""
        seen: list[str] = []
        for scene in self.scenes:
            if scene.transition_out.type not in seen:
                seen.append(scene.transition_out.type)
        return seen


# Edits -------------------------------------------------------------------------------------


class EditChoices(BaseModel):
    """``POST /api/edits/{id}/edit``: the simple choices of the editor (docs/CONTRACT-v0.1.md
    section 4). A missing field means "leave as is"."""

    presets: list[str] | None = Field(default=None, description="the sizes to render")
    music_path: str | None = Field(
        default=None,
        description="a file in the edit's 01_media folder (or an absolute path); '' removes "
        "the music",
    )
    captions_enabled: bool | None = None
    popups_enabled: bool | None = None
    music_gain_db: float | None = Field(default=None, ge=-60, le=12)
    music_duck_db: float | None = Field(default=None, ge=-60, le=0)
    rebuild: bool = Field(
        default=False, description="build the timeline again from the scene plan and timing"
    )

    @property
    def changes_music(self) -> bool:
        return "music_path" in self.model_fields_set

    @property
    def is_empty(self) -> bool:
        return not (
            self.presets is not None
            or self.changes_music
            or self.captions_enabled is not None
            or self.popups_enabled is not None
            or self.music_gain_db is not None
            or self.music_duck_db is not None
            or self.rebuild
        )


# Review payload ------------------------------------------------------------------------------


class RenderOutput(BaseModel):
    """One render (``07_edit/proxy.mp4`` or ``final_<preset>.mp4``) and where it stands."""

    preset: str
    status: RenderStatus = "not_rendered"
    path: str | None = Field(default=None, description="relative to the project folder")
    play_url: str | None = None
    progress: float | None = None
    error: str | None = None
    duration_s: float | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    rendered_at: str | None = None
    # Transitions v2 (cached scene clips): what the last render could reuse.
    scene_clips_total: int | None = None
    scene_clips_reused: int | None = None
    reuse_note: str | None = Field(
        default=None, description='plain English, e.g. "Reused 2 of 3 scene clips."'
    )


class MusicTrackInfo(BaseModel):
    path: str
    name: str
    license_ok: bool = False
    duration_s: float | None = None


class TimelineSummary(BaseModel):
    scene_count: int = 0
    duration_s: float = 0.0
    transitions: list[str] = []
    popup_count: int = 0
    caption_cues: int = 0
    fps: int = 30
    width: int = 0
    height: int = 0
    aspect: Aspect | None = None


class EditSceneView(BaseModel):
    index: int
    start_s: float
    end_s: float
    duration_s: float = 0.0
    image_path: str | None = None
    image_url: str | None = None
    transition: str | None = None
    transition_s: float | None = None
    motion: str | None = None
    popup_text: str | None = None
    locked: bool = False


class EditReviewPayload(BaseModel):
    stage: Literal["edit"] = "edit"
    proxy_path: str | None = None
    proxy_url: str | None = None
    timeline: Timeline | None = None
    summary: TimelineSummary = TimelineSummary()
    scenes: list[EditSceneView] = []
    renders: list[RenderOutput] = []
    presets: list[str] = []
    available_presets: list[str] = []
    enable_4k: bool | None = None
    music_tracks: list[MusicTrackInfo] = []
    music_path: str | None = None
    music_license_ok: bool = False
    captions_enabled: bool = True
    popups_enabled: bool = True
    warnings: list[str] = []
    gate_results: list[dict[str, Any]] = []
    render_log_path: str | None = None
    timing_source: str | None = None
    render_notes: list[str] = Field(
        default=[],
        description="plain-English notes on the last renders (clips reused, parallax "
        "fallbacks)",
    )
    # M5.1: what the live preview plays (also inside ``timeline``).
    stem_url: str | None = Field(
        default=None,
        description="07_edit/music_ducked.wav through the files route (?v=<stem key>)",
    )
    envelope_url: str | None = Field(
        default=None,
        description="07_edit/music_envelope.json through the files route (?v=<stem key>)",
    )
    preview_gain_db: float | None = None
    # 9Sigma Editor (docs/CONTRACT-v0.1.md sections 4 and 5)
    quality: str = Field(default="standard", description="standard, high or best")
    upload_preset: str = Field(default="none", description="none, youtube_1080p or youtube_4k")
    h265_copy: bool = False
    music_licence_note: str = ""
    music_folder: str | None = Field(
        default=None, description="where the edit's music files are (01_media)"
    )
    checks: list[dict[str, Any]] = Field(
        default=[], description="the editor's own checks (caption speed, missing pictures)"
    )
    rendering: bool = False
    render_message: str = ""
    render_pct: float | None = None
    last_render_error: str | None = None


# Live preview routes (api/preview.py, docs/M5-CONTRACT.md section 2) ----------------------------


class BundleWord(BaseModel):
    w: str
    start_s: float
    end_s: float


class BundleSentence(BaseModel):
    """A sentence of ``05_voice/timing.json``: times are seconds in the voice file."""

    id: str
    text: str
    start_s: float
    end_s: float
    words: list[BundleWord] = []


class BundleSection(BaseModel):
    """A part of the script and the scenes it covers (``start_scene`` .. ``end_scene``,
    0-based, both included)."""

    id: str
    name: str
    start_scene: int
    end_scene: int


class BundlePicture(BaseModel):
    scene: int
    path: str
    width: int = 0
    height: int = 0
    thumb_url: str | None = Field(
        default=None, description="128 px tall JPEG; null when the picture is missing"
    )


class BundleFont(BaseModel):
    family: str
    url: str


class PreviewGainCurve(BaseModel):
    """The gain the renderer's ``loudnorm`` gives the mix (voice + music stem), measured on
    that mix: ``gain_db[i]`` covers ``i * step_s`` .. ``(i + 1) * step_s`` seconds."""

    step_s: float = 0.1
    gain_db: list[float] = []


class BundleMedia(BaseModel):
    voice_url: str | None = None
    stem_url: str | None = Field(
        default=None, description="null until the music has been prepared for this timeline"
    )
    envelope_url: str | None = None
    fonts: list[BundleFont] = []
    # Additive to the contract's shape (docs/M5-CONTRACT.md 8.7.5):
    music_url: str | None = Field(
        default=None,
        description="the timeline's own music track (GET /media/music?v=...), which the "
        "preview plays with the envelope applied live while the stem is not current; null "
        "without music or when the track is not on this PC",
    )


class TimelineBundle(BaseModel):
    """``GET /api/projects/{id}/timeline``: everything the live preview needs in one call."""

    timeline: Timeline
    revision: int
    sentences: list[BundleSentence] = []
    sections: list[BundleSection] = []
    pictures: list[BundlePicture] = []
    media: BundleMedia = BundleMedia()
    preview_gain_db: float | None = None
    # Additive to the contract's shape:
    virtual: bool = Field(
        default=False,
        description="true before the edit step has built timeline.json: the timeline is "
        "worked out from the storyboard, the voice timing and the pictures (nothing is saved)",
    )
    timing_estimated: bool = Field(
        default=False,
        description="true when there is no voice yet and the times are the storyboard's "
        "estimates",
    )
    cue_words: dict[str, list[BundleWord]] = Field(
        default={},
        description="per caption cue id, its words with the times render/captions.py uses "
        "for the active-word and single-word looks",
    )
    preview_gain_curve: PreviewGainCurve | None = Field(
        default=None,
        description="loudnorm's gain over the video when it has been measured for this "
        "sound (the preview follows it instead of the one preview_gain_db)",
    )


class PeaksDoc(BaseModel):
    """``GET /api/projects/{id}/media/peaks/{voice|music}.json``: the lowest and highest
    sample of every 10 ms (-1 .. 1, all channels)."""

    step_s: float = 0.01
    min: list[float] = []
    max: list[float] = []
    duration_s: float = 0.0
    source: str = Field(default="", description="voice, stem or track")


class PopupPreviewRequest(BaseModel):
    """``POST /api/projects/{id}/popups/preview``."""

    text: str = Field(min_length=1, max_length=300)
    style: str = ""
    position: str = "bottom-left"
    anim: PopupAnim = "slide_left"
    aspect: Aspect = "16:9"
    height: int = Field(default=540, ge=90, le=2160, description="preview frame height")


class PopupPreviewResult(BaseModel):
    png_url: str
    x: int
    y: int
    w: int
    h: int


class MusicStemResult(BaseModel):
    """``POST /api/projects/{id}/music/stem``."""

    stem_url: str | None = None
    envelope_url: str | None = None
    preview_gain_db: float | None = None
