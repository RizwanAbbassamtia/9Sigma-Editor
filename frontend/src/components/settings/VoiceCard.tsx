import { KeyRound, Mic2, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { errorMessage } from "../../api/client";
import { useSaveVoiceKey, useVoiceStatus, useVoices } from "../../api/voice";
import { VOICE_PROVIDER_LABEL } from "../../types/voice";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

/** Settings > AI voice (docs/CONTRACT-v0.2.md section 5): the ElevenLabs API key. */
export function VoiceCard() {
  const status = useVoiceStatus();
  const save = useSaveVoiceKey();
  const { toast } = useToast();
  const [key, setKey] = useState("");
  const [testing, setTesting] = useState(false);
  const voices = useVoices(testing && Boolean(status.data?.key_set));
  const provider = status.data?.provider_label ?? VOICE_PROVIDER_LABEL;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = key.trim();
    if (!value) return;
    save.mutate(value, {
      onSuccess: () => {
        setKey("");
        setTesting(false);
        toast({ tone: "success", title: "The API key is saved on this computer" });
      },
      onError: (error) => toast({ tone: "error", title: "The key was not saved", description: errorMessage(error) }),
    });
  };

  const remove = () =>
    save.mutate("", {
      onSuccess: () => {
        setTesting(false);
        toast({ tone: "success", title: "The API key was removed" });
      },
      onError: (error) => toast({ tone: "error", title: "The key was not removed", description: errorMessage(error) }),
    });

  return (
    <Card id="voice" title="AI voice (optional)" description={`An extra, not a requirement: the editor does everything without it. If you want a narration or a sound clip made from text, add your own ${provider} key here. It is kept on this computer only and used only when you press "Make the voice".`}>
      <form onSubmit={submit} className="flex max-w-md flex-col gap-4">
        {status.data?.key_set ? (
          <Notice tone="ok" title={`A ${provider} key is saved (${status.data.key_hint})`}>
            Paste a new key below to replace it.
          </Notice>
        ) : (
          <Notice tone="info" title="No key yet">
            Sign in at elevenlabs.io, open your profile and copy your API key. Paste it here.
          </Notice>
        )}
        <Field label={`${provider} API key`} htmlFor="voice-key">
          <Input id="voice-key" type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} placeholder="Paste the key" />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="sm" icon={<KeyRound />} disabled={!key.trim()} loading={save.isPending}>
            Save the key
          </Button>
          <Button type="button" variant="secondary" size="sm" icon={<Mic2 />} disabled={!status.data?.key_set} loading={voices.isFetching} onClick={() => setTesting(true)}>
            Test the key
          </Button>
          {status.data?.key_set ? (
            <Button type="button" variant="ghost" size="sm" icon={<Trash2 />} onClick={remove} disabled={save.isPending}>
              Remove the key
            </Button>
          ) : null}
        </div>
        {testing && voices.data ? <p className="text-sm text-ok">The key works: {voices.data.length} voice{voices.data.length === 1 ? "" : "s"} available.</p> : null}
        {testing && voices.isError ? <p className="text-sm text-fail">{errorMessage(voices.error)}</p> : null}
      </form>
    </Card>
  );
}
