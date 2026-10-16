/** react-query cache keys, one place so invalidation stays consistent. */
export const queryKeys = {
  settings: ["settings"] as const,
  doctor: ["doctor"] as const,
  system: ["system"] as const,
  update: ["update"] as const,
  renders: ["renders"] as const,

  // Edits: ["projects"] invalidates every list, detail and payload at once. (The name
  // "projects" is kept because the copied preview code builds its keys on it.)
  projects: ["projects"] as const,
  projectLists: ["projects", "list"] as const,
  projectList: () => ["projects", "list"] as const,
  project: (id: string) => ["projects", "detail", id] as const,
  /** the editor payload (timeline, renders, checks) of one edit */
  editPayload: (id: string) => ["projects", "detail", id, "edit"] as const,
  /** kept for the copied code that still asks for a "stage" payload: the edit payload */
  stagePayload: (id: string, _stage: string) => ["projects", "detail", id, "edit"] as const,
  timing: (id: string) => ["projects", "detail", id, "timing"] as const,
  captions: (id: string) => ["projects", "detail", id, "captions"] as const,
  renderStatus: (id: string) => ["projects", "detail", id, "render-status"] as const,
  /** v0.2: the media library of one edit */
  media: (id: string) => ["projects", "detail", id, "media"] as const,
  voiceStatus: ["voice", "status"] as const,
  voices: ["voice", "voices"] as const,
};
