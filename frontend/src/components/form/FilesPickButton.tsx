import { useRef, type ReactNode } from "react";

import { buttonClasses, type ButtonSize, type ButtonVariant } from "../ui/Button";
import { Spinner } from "../ui/Spinner";

export interface FilesPickButtonProps {
  /** the `accept` attribute of the file input, e.g. ".png,.jpg,.mp4" */
  accept: string;
  onPick: (files: File[]) => void;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}

/** Like FilePickButton, for several files at once (the pictures and clips of a new edit). */
export function FilesPickButton({ accept, onPick, variant = "secondary", size = "md", icon, loading = false, disabled = false, className, title, children }: FilesPickButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" title={title} disabled={disabled || loading} className={buttonClasses(variant, size, className)} onClick={() => inputRef.current?.click()}>
        {loading ? <Spinner className="size-4" /> : icon}
        {children}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple
        tabIndex={-1}
        aria-hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length > 0) onPick(files);
          event.target.value = "";
        }}
      />
    </>
  );
}
