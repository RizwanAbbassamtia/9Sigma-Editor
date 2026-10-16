import { ArrowDown, ArrowUp, FilePlus2, Image as ImageIcon, Music, Subtitles, Trash2, Video, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, errorMessage } from "../../api/client";
import { useCreateEdit } from "../../api/projects";
import { editorPath } from "../../layout/nav";
import { compareNatural, fileKind, FORMAT_LABELS, LANGUAGES, PROJECT_FORMATS, type EditInput, type ItemOrder, type ProjectFormat } from "../../types/project";
import { formatBytes } from "../edit/editUtils";
import { FilePickButton } from "../form/FilePickButton";
import { FilesPickButton } from "../form/FilesPickButton";
import { SegmentedControl } from "../form/SegmentedControl";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Input, Select, Textarea } from "../ui/Input";
import { Notice } from "../ui/States";

export const PICTURE_ACCEPT = ".png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff";
export const CLIP_ACCEPT = ".mp4,.mov,.m4v,.mkv,.webm,.avi";
export const MEDIA_ACCEPT = `${PICTURE_ACCEPT},${CLIP_ACCEPT}`;
export const AUDIO_ACCEPT = "audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.wma,.aif,.aiff";
export const CAPTIONS_ACCEPT = ".srt,.vtt";

interface Item {
  key: number;
  file: File;
  caption: string;
  keep_sound: boolean;
}

const ORDER_OPTIONS: ReadonlyArray<{ value: ItemOrder; label: string; description: string }> = [
  { value: "as_given", label: "In this order", description: "The order below is the order of the video" },
  { value: "by_name", label: "By file name", description: "scene_1, scene_2, ... scene_10 (numbers count as numbers)" },
];

let nextKey = 1;

/** The "New edit from files" form (docs/CONTRACT-v0.1.md section 3): one dialog, one request. */
export function NewEditDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const create = useCreateEdit();
  const [name, setName] = useState("");
  const [format, setFormat] = useState<ProjectFormat>("long");
  const [language, setLanguage] = useState("English");
  const [order, setOrder] = useState<ItemOrder>("as_given");
  const [items, setItems] = useState<Item[]>([]);
  const [narration, setNarration] = useState<File | null>(null);
  const [music, setMusic] = useState<File | null>(null);
  const [captions, setCaptions] = useState<File | null>(null);
  const [captionText, setCaptionText] = useState("");
  const [licenceNote, setLicenceNote] = useState("");
  const [keepClipSound, setKeepClipSound] = useState(false);

  const shown = useMemo(() => (order === "by_name" ? [...items].sort((a, b) => compareNatural(a.file.name, b.file.name)) : items), [items, order]);
  const rejected = useMemo(() => items.filter((item) => fileKind(item.file.name) === "other"), [items]);
  const hasClips = items.some((item) => fileKind(item.file.name) === "clip");
  const ready = name.trim().length > 0 && items.length > 0 && rejected.length === 0 && !create.isPending;

  const reset = () => {
    setName("");
    setFormat("long");
    setLanguage("English");
    setOrder("as_given");
    setItems([]);
    setNarration(null);
    setMusic(null);
    setCaptions(null);
    setCaptionText("");
    setLicenceNote("");
    setKeepClipSound(false);
    create.reset();
  };

  const close = () => {
    if (create.isPending) return;
    reset();
    onClose();
  };

  const addFiles = (files: File[]) => {
    setItems((current) => [...current, ...files.map((file) => ({ key: nextKey++, file, caption: "", keep_sound: false }))]);
    if (!name.trim() && files[0]) setName(files[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim());
  };

  const patchItem = (key: number, changes: Partial<Item>) => setItems((current) => current.map((item) => (item.key === key ? { ...item, ...changes } : item)));
  const removeItem = (key: number) => setItems((current) => current.filter((item) => item.key !== key));
  const move = (key: number, step: -1 | 1) =>
    setItems((current) => {
      const index = current.findIndex((item) => item.key === key);
      const target = index + step;
      if (index === -1 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target]!, next[index]!];
      return next;
    });

  const submit = () => {
    if (!ready) return;
    const form: EditInput = {
      name: name.trim(),
      format,
      language,
      order,
      items: shown.map((item) => ({ file: item.file.name, caption: item.caption.trim(), keep_sound: item.keep_sound })),
      caption_text: captionText,
      music_licence_note: licenceNote.trim(),
      keep_clip_sound: keepClipSound,
    };
    create.mutate(
      { form, files: { items: shown.map((item) => item.file), narration, music, captions } },
      {
        onSuccess: (project) => {
          reset();
          onClose();
          navigate(editorPath(project.id));
        },
      },
    );
  };

  const fieldError = (path: string): string | undefined => {
    if (!(create.error instanceof ApiError)) return undefined;
    return create.error.fieldErrors.find((error) => error.path === path || error.path.startsWith(`${path}.`))?.message;
  };

  return (
    <Dialog
      open={open}
      title="New edit from files"
      description="Pick the pictures and clips of the video, and if you have them the narration, the music and the captions. Everything else can be changed in the editor."
      onClose={close}
      className="max-w-3xl!"
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" icon={<FilePlus2 />} onClick={submit} disabled={!ready} loading={create.isPending}>
            Make the edit
          </Button>
        </>
      }
    >
      <div className="flex max-h-[65vh] flex-col gap-5 overflow-y-auto pr-1">
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px_180px]">
          <Field label="Name" htmlFor="new-edit-name" required error={fieldError("name")}>
            <Input id="new-edit-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="What this video is about" data-autofocus />
          </Field>
          <Field label="Shape" htmlFor="new-edit-format">
            <Select id="new-edit-format" value={format} onChange={(event) => setFormat(event.target.value as ProjectFormat)}>
              {PROJECT_FORMATS.map((option) => (
                <option key={option} value={option}>
                  {FORMAT_LABELS[option]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Language" htmlFor="new-edit-language" hint="Picks the caption font and rules.">
            <Select id="new-edit-language" value={language} onChange={(event) => setLanguage(event.target.value)}>
              {LANGUAGES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <section className="flex flex-col gap-3 rounded-md border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-ink">Pictures and clips</h3>
              <p className="text-xs text-ink-muted">One scene per file. Pictures: png, jpg, webp. Clips: mp4, mov.</p>
            </div>
            <div className="flex items-center gap-2">
              <SegmentedControl options={ORDER_OPTIONS} value={order} onChange={setOrder} aria-label="Scene order" />
              <FilesPickButton accept={MEDIA_ACCEPT} onPick={addFiles} variant="primary" size="sm" icon={<ImageIcon />}>
                Add files
              </FilesPickButton>
            </div>
          </div>
          {fieldError("items") ? <p className="text-xs text-fail">{fieldError("items")}</p> : null}
          {rejected.length > 0 ? (
            <Notice tone="warn" title="Some files are not pictures or clips">
              {rejected.map((item) => item.file.name).join(", ")}. Remove them, or add them as the narration, music or captions below.
            </Notice>
          ) : null}
          {shown.length === 0 ? (
            <p className="rounded-md border border-dashed border-line px-4 py-6 text-center text-[13px] text-ink-faint">No files yet. Add at least one picture or clip.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {shown.map((item, position) => {
                const kind = fileKind(item.file.name);
                return (
                  <li key={item.key} className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface px-3 py-2">
                    <span className="w-6 text-right text-xs tabular-nums text-ink-faint">{position + 1}</span>
                    {kind === "clip" ? <Video className="size-4 shrink-0 text-ink-faint" aria-hidden /> : <ImageIcon className="size-4 shrink-0 text-ink-faint" aria-hidden />}
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink" title={item.file.name}>
                      {item.file.name}
                      <span className="ml-2 text-xs text-ink-faint">{formatBytes(item.file.size)}</span>
                    </span>
                    {kind === "other" ? <Badge tone="fail">Not a picture or clip</Badge> : null}
                    <Input
                      className="h-8 w-56 text-[13px]"
                      value={item.caption}
                      placeholder="Popup text (optional)"
                      aria-label={`Popup text for ${item.file.name}`}
                      onChange={(event) => patchItem(item.key, { caption: event.target.value })}
                    />
                    {kind === "clip" ? (
                      <label className="flex items-center gap-1.5 text-xs text-ink-muted">
                        <input type="checkbox" className="accent-accent" checked={item.keep_sound || keepClipSound} disabled={keepClipSound} onChange={(event) => patchItem(item.key, { keep_sound: event.target.checked })} />
                        Keep its sound
                      </label>
                    ) : null}
                    {order === "as_given" ? (
                      <>
                        <Button variant="ghost" size="sm" icon={<ArrowUp />} aria-label="Move up" onClick={() => move(item.key, -1)} disabled={position === 0} />
                        <Button variant="ghost" size="sm" icon={<ArrowDown />} aria-label="Move down" onClick={() => move(item.key, 1)} disabled={position === shown.length - 1} />
                      </>
                    ) : null}
                    <Button variant="ghost" size="sm" icon={<Trash2 />} aria-label={`Remove ${item.file.name}`} onClick={() => removeItem(item.key)} />
                  </li>
                );
              })}
            </ol>
          )}
          {hasClips ? (
            <label className="flex items-center gap-2 text-[13px] text-ink">
              <input type="checkbox" className="accent-accent" checked={keepClipSound} onChange={(event) => setKeepClipSound(event.target.checked)} />
              Keep the sound of every clip
            </label>
          ) : null}
        </section>

        <section className="grid gap-4 md:grid-cols-3">
          <OptionalFile
            icon={<Music />}
            label="Narration"
            hint="Your voice recording. Without one the scenes get 6 seconds each."
            accept={AUDIO_ACCEPT}
            file={narration}
            onPick={setNarration}
            onClear={() => setNarration(null)}
            error={fieldError("narration")}
          />
          <OptionalFile icon={<Music />} label="Background music" hint="Plays under the voice, dipped while the narration speaks." accept={AUDIO_ACCEPT} file={music} onPick={setMusic} onClear={() => setMusic(null)} error={fieldError("music")} />
          <OptionalFile
            icon={<Subtitles />}
            label="Caption file"
            hint="An SRT or VTT file. Its times become the sentence times."
            accept={CAPTIONS_ACCEPT}
            file={captions}
            onPick={setCaptions}
            onClear={() => setCaptions(null)}
            error={fieldError("captions")}
          />
        </section>

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Caption text" htmlFor="new-edit-captions" hint={captions ? "The caption file above is used instead of this text." : "What is said, one sentence per line or as a paragraph. Spread over the narration when there is no caption file."}>
            <Textarea id="new-edit-captions" rows={4} value={captionText} onChange={(event) => setCaptionText(event.target.value)} disabled={Boolean(captions)} />
          </Field>
          <Field label="Music licence note" htmlFor="new-edit-licence" hint="Where the music comes from and the licence it has. Shown in the checks and written into the export.">
            <Textarea id="new-edit-licence" rows={4} value={licenceNote} onChange={(event) => setLicenceNote(event.target.value)} />
          </Field>
        </div>

        {create.isError && !(create.error instanceof ApiError && create.error.fieldErrors.length > 0) ? (
          <Notice tone="fail" title="The edit could not be made">
            {errorMessage(create.error)}
          </Notice>
        ) : null}
      </div>
    </Dialog>
  );
}

function OptionalFile({
  icon,
  label,
  hint,
  accept,
  file,
  onPick,
  onClear,
  error,
}: {
  icon: React.ReactNode;
  label: string;
  hint: string;
  accept: string;
  file: File | null;
  onPick: (file: File) => void;
  onClear: () => void;
  error?: string | undefined;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-md border border-line p-3">
      <p className="text-[13px] font-medium text-ink">{label}</p>
      {file ? (
        <p className="flex items-center gap-2 text-[13px] text-ink">
          <span className="min-w-0 flex-1 truncate" title={file.name}>
            {file.name}
          </span>
          <button type="button" onClick={onClear} className="rounded p-0.5 text-ink-faint hover:text-ink" aria-label={`Remove the ${label.toLowerCase()} file`}>
            <X className="size-3.5" aria-hidden />
          </button>
        </p>
      ) : (
        <FilePickButton accept={accept} onPick={onPick} size="sm" icon={icon}>
          Choose a file
        </FilePickButton>
      )}
      <p className="text-xs text-ink-faint">{hint}</p>
      {error ? <p className="text-xs text-fail">{error}</p> : null}
    </div>
  );
}
