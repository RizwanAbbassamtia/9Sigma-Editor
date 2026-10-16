import { afterEach, describe, expect, it, vi } from "vitest";

import { PictureStore } from "../pictures";

interface Call {
  url: string;
  signal: AbortSignal | undefined;
  resolve: (response: Response) => void;
}

/** A fetch whose answers the test gives by hand; an aborted request rejects like the browser's. */
function handFetch() {
  const calls: Call[] = [];
  const fetchFn = ((url: string, init?: RequestInit) =>
    new Promise<Response>((resolve, reject) => {
      const signal = init?.signal ?? undefined;
      calls.push({ url, signal, resolve });
      signal?.addEventListener("abort", () => reject(new DOMException("The user aborted a request.", "AbortError")));
    })) as unknown as typeof fetch;
  return { calls, fetchFn };
}

const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

function picture() {
  return { width: 4, height: 2, close: vi.fn() } as unknown as ImageBitmap;
}

describe("PictureStore", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stops the downloads the playhead has left and forgets them", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn(async () => picture()));
    const { calls, fetchFn } = handFetch();
    const loaded: string[] = [];
    const store = new PictureStore({ fileUrl: (path) => `file:${path}`, fetch: fetchFn, onLoad: (path) => loaded.push(path) });
    store.want("a.png", 100, [4, 2]);
    store.want("b.png", 100, [4, 2]);
    expect(calls.map((call) => call.url)).toEqual(["file:a.png", "file:b.png"]);

    store.cancel(new Set(["a.png"]));
    expect(calls[0]!.signal?.aborted).toBe(false);
    expect(calls[1]!.signal?.aborted).toBe(true);
    await flush();
    // not "missing" (that would show the slate): simply not there, and nothing was announced
    expect(store.get("b.png")).toBeNull();
    expect(loaded).toEqual([]);
    // the next file is not tried after a stop
    expect(calls).toHaveLength(2);

    calls[0]!.resolve(new Response(new Uint8Array([1, 2, 3])));
    await flush();
    expect(store.get("a.png")).toMatchObject({ path: "a.png", imageWidth: 4, imageHeight: 2 });
    expect(loaded).toEqual(["a.png"]);

    // asked for again, it loads from the start
    store.want("b.png", 100, [4, 2]);
    expect(calls).toHaveLength(3);
    expect(calls[2]!.url).toBe("file:b.png");
  });

  it("lets a picture whose bytes are in finish decoding", async () => {
    let finish: (bitmap: ImageBitmap) => void = () => undefined;
    vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise<ImageBitmap>((resolve) => (finish = resolve))));
    const { calls, fetchFn } = handFetch();
    const loaded: string[] = [];
    const store = new PictureStore({ fileUrl: (path) => `file:${path}`, fetch: fetchFn, onLoad: (path) => loaded.push(path) });
    store.want("c.png", 100, null);
    calls[0]!.resolve(new Response(new Uint8Array([1])));
    await flush();
    store.cancel(new Set());
    expect(calls[0]!.signal?.aborted).toBe(false);
    finish(picture());
    await flush();
    expect(store.get("c.png")).toMatchObject({ path: "c.png" });
    expect(loaded).toEqual(["c.png"]);
  });

  it("still marks a picture that cannot be read as missing", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn(async () => picture()));
    const { calls, fetchFn } = handFetch();
    const loaded: string[] = [];
    const store = new PictureStore({ fileUrl: (path) => `file:${path}`, fetch: fetchFn, onLoad: (path) => loaded.push(path) });
    store.want("d.png", 100, null);
    calls[0]!.resolve(new Response(null, { status: 404 }));
    await flush();
    expect(store.get("d.png")).toBe("missing");
    expect(loaded).toEqual(["d.png"]);
  });

  it("stops every download when it is destroyed", () => {
    const { calls, fetchFn } = handFetch();
    const store = new PictureStore({ fileUrl: (path) => `file:${path}`, fetch: fetchFn, onLoad: () => undefined });
    store.want("e.png", 100, null);
    store.destroy();
    expect(calls[0]!.signal?.aborted).toBe(true);
  });
});
