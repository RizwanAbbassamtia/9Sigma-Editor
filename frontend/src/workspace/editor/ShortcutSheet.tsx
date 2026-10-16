import { X } from "lucide-react";

/** One table for the shortcut sheet and the tooltips (docs/design/EDITOR-DESIGN.md section 6). */
export const SHORTCUTS: ReadonlyArray<{ keys: string; action: string }> = [
  { keys: "Space, K", action: "Play / pause" },
  { keys: "Left / Right", action: "One frame back / forward" },
  { keys: "Shift+Left / Shift+Right", action: "One second back / forward" },
  { keys: "Up / Down", action: "Previous / next cut" },
  { keys: "Ctrl+Up / Ctrl+Down", action: "Previous / next sentence" },
  { keys: "Home / End", action: "Go to start / end" },
  { keys: "I / O", action: "Mark in / out" },
  { keys: "Alt+X", action: "Clear in and out" },
  { keys: "Ctrl+L", action: "Loop playback" },
  { keys: "S, Ctrl+B, Ctrl+K", action: "Split the scene at the playhead" },
  { keys: "Q / W", action: "Move the selected scene's start / end cut to the playhead" },
  { keys: "Delete / Shift+Delete", action: "Merge the scene into the previous / next one; delete a popup, marker or caption" },
  { keys: "[ / ]", action: "Select the previous / next clip on the lane" },
  { keys: "M", action: "Add a marker" },
  { keys: "T", action: "Add a popup at the playhead" },
  { keys: "Ctrl+Z / Ctrl+Y", action: "Undo / redo" },
  { keys: "Esc", action: "Deselect" },
  { keys: "N", action: "Snapping on / off" },
  { keys: "= / -", action: "Zoom in / out" },
  { keys: "Shift+Z or \\", action: "Fit the whole video" },
  { keys: "Ctrl+E", action: "Open the Render menu" },
  { keys: "?", action: "This sheet" },
];

export function ShortcutSheet({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div role="dialog" aria-label="Keyboard shortcuts" className="max-h-[80vh] w-[560px] overflow-y-auto rounded-md border border-ed-line-strong bg-ed-panel p-4 shadow-[0_12px_32px_rgb(0_0_0/0.5)]">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[14px] font-semibold text-ink">Keyboard shortcuts</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="ws-focus grid size-7 place-items-center rounded text-ink-faint hover:bg-ed-raised hover:text-ink">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <table className="w-full text-[12px]">
          <tbody>
            {SHORTCUTS.map((row) => (
              <tr key={row.keys} className="border-t border-ed-line">
                <td className="w-56 py-1.5 pr-3 font-mono text-[11px] text-accent-text">{row.keys}</td>
                <td className="py-1.5 text-ink">{row.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-[11px] text-ink-faint">Shortcuts work while the focus is in the workspace, not while typing in a text field. Esc leaves a text field.</p>
      </div>
    </div>
  );
}
