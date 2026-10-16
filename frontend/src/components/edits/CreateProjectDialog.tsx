import { Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import { errorMessage } from "../../api/client";
import { useCreateBlankEdit } from "../../api/media";
import { editorPath } from "../../layout/nav";
import { FORMAT_LABELS, LANGUAGES, PROJECT_FORMATS, type ProjectFormat } from "../../types/project";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Input, Select } from "../ui/Input";
import { Notice } from "../ui/States";

/** "Create project" (docs/CONTRACT-v0.2.md section 1): a name and a shape, then the Edit room. */
export function CreateProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const create = useCreateBlankEdit();
  const [name, setName] = useState("");
  const [format, setFormat] = useState<ProjectFormat>("long");
  const [language, setLanguage] = useState("English");
  const ready = name.trim().length > 0 && !create.isPending;

  const close = () => {
    if (create.isPending) return;
    create.reset();
    onClose();
  };

  const submit = () => {
    if (!ready) return;
    create.mutate(
      { name: name.trim(), format, language },
      {
        onSuccess: (project) => {
          setName("");
          onClose();
          navigate(editorPath(project.id));
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      title="Create project"
      description="An empty project. You import pictures, clips and sound in the editor and build the video there."
      onClose={close}
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Plus />} onClick={submit} disabled={!ready} loading={create.isPending}>
            Create and open
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" htmlFor="create-project-name" required>
          <Input
            id="create-project-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="What this video is about"
            data-autofocus
            onKeyDown={(event) => {
              if (event.key === "Enter") submit();
            }}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Shape" htmlFor="create-project-format" hint="16:9 for YouTube, 9:16 for Shorts and Reels.">
            <Select id="create-project-format" value={format} onChange={(event) => setFormat(event.target.value as ProjectFormat)}>
              {PROJECT_FORMATS.map((option) => (
                <option key={option} value={option}>
                  {FORMAT_LABELS[option]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Language" htmlFor="create-project-language" hint="Picks the caption font and rules.">
            <Select id="create-project-language" value={language} onChange={(event) => setLanguage(event.target.value)}>
              {LANGUAGES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {create.isError ? (
          <Notice tone="fail" title="The project could not be made">
            {errorMessage(create.error)}
          </Notice>
        ) : null}
      </div>
    </Dialog>
  );
}
