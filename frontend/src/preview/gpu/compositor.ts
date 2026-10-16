/**
 * The WebGL2 compositor (PixiJS 8): Ken Burns as a textured quad whose texture coordinates are the
 * camera window (output pixel u shows `x + (u + 0.5) w / W`, mipmapped), 2.5D parallax as a 64 x 36
 * mesh, transitions as GLSL passes over two render textures (A and B drawn at the same video
 * frame), popups and captions as sprites on top. A lost WebGL context is reported to the engine,
 * which shows "Restarting the preview..." and falls back to Canvas 2D when it does not come back.
 */
import { Container, GlProgram, Mesh, MeshGeometry, RenderTexture, Shader, Sprite, Texture, TextureSource, UniformGroup, WebGLRenderer, CanvasSource, ImageSource } from "pixi.js";

import type { Compositor, DrawInput, Overlay, SceneSource, SlateImage } from "../compositor";
import type { Rect } from "../motion";
import { parallaxMesh, type MeshFrame, type ParallaxPlan } from "../parallax";
import type { Picture } from "../pictures";
import { PASS_VERTEX, fragmentSource, type TransitionFilter, type TransitionFrame, type UniformSpec } from "../transitions/common";
import { TRANSITION_FILTERS, resolveTransition } from "../transitions/registry";
import type { FrameState, SceneRef } from "../types";
import { BLUR_FRAGMENT, DOWNSAMPLE_FRAGMENT, FIELD_FRAGMENT, KB_FRAGMENT, LUMA_FRAGMENT, blurPlan, kenBurnsSampling, kernelRadius } from "./passes";

type RT = RenderTexture;

interface PassProgram {
  shader: Shader;
  uniforms: UniformGroup;
}

function defaultValue(spec: UniformSpec): number | Float32Array | Int32Array {
  const lanes: Record<string, number> = { "vec2<f32>": 2, "vec3<f32>": 3, "vec4<f32>": 4, "mat3x3<f32>": 9 };
  const count = (lanes[spec.type] ?? 1) * Math.max(spec.size ?? 1, 1);
  if (count === 1 && !spec.size) return 0;
  return new Float32Array(count);
}

/** Overlay sprites (popups, the caption canvas) kept while off screen. */
const OVERLAY_KEEP = 24;
/** Overlay textures put aside before the oldest is destroyed. */
const OVERLAY_RETIRED_MAX = 16;

function quadGeometry(): MeshGeometry {
  return new MeshGeometry({
    positions: new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]),
    uvs: new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]),
    indices: new Uint32Array([0, 1, 2, 0, 2, 3]),
  });
}

function setQuad(geometry: MeshGeometry, width: number, height: number): void {
  const positions = geometry.positions;
  if (positions[2] === width && positions[5] === height) return;
  geometry.positions = new Float32Array([0, 0, width, 0, width, height, 0, height]);
}

export interface GpuCompositorOptions {
  onContextLost: () => void;
  onContextRestored: () => void;
  onError: (message: string) => void;
}

export class GpuCompositor implements Compositor {
  readonly kind = "webgl2" as const;
  private width = 2;
  private height = 2;
  private readonly stage = new Container();
  private readonly holders: [Container, Container] = [new Container(), new Container()];
  private readonly kbMeshes: [Mesh<MeshGeometry, Shader>, Mesh<MeshGeometry, Shader>];
  private readonly kbShaders: [Shader, Shader];
  private readonly kbUniforms: [UniformGroup, UniformGroup];
  private readonly parallaxMeshes: [Mesh | null, Mesh | null] = [null, null];
  private readonly frontMeshes: [Mesh | null, Mesh | null] = [null, null];
  private readonly layerHolders: [Container, Container] = [new Container(), new Container()];
  private readonly parallaxFrames: [MeshFrame | undefined, MeshFrame | undefined] = [undefined, undefined];
  private readonly frontFrames: [MeshFrame | undefined, MeshFrame | undefined] = [undefined, undefined];
  /** the plates' textures of the parallax plans around the playhead (released with their plan) */
  private readonly plateTextures = new Map<ParallaxPlan, { back: Texture; front: Texture }>();
  private readonly slateSprites: [Sprite, Sprite] = [new Sprite(Texture.EMPTY), new Sprite(Texture.EMPTY)];
  private readonly composite: Mesh<MeshGeometry, Shader>;
  private readonly passMesh: Mesh<MeshGeometry, Shader>;
  private readonly passGeometry: MeshGeometry;
  private readonly fade = new Sprite(Texture.WHITE);
  private readonly overlaySprites = new Map<string, { sprite: Sprite; texture: Texture; version: number; image: unknown; used: number }>();
  /** overlay textures taken off screen, destroyed only when too many pile up (see `retire`) */
  private readonly retired: Texture[] = [];
  private overlayClock = 0;
  private readonly programs = new Map<string, { shader: Shader; pre: Shader | null; common: UniformGroup; params: UniformGroup; filter: TransitionFilter }>();
  private readonly passes = new Map<string, PassProgram>();
  private readonly targets = new Map<string, RT>();
  private readonly pictures = new Map<string, { texture: Texture; version: number }>();
  /** one slate texture per scene slot, updated when the engine redraws the slot's canvas */
  private readonly slates: [{ canvas: SlateImage["canvas"]; texture: Texture; version: number } | null, { canvas: SlateImage["canvas"]; texture: Texture; version: number } | null] = [null, null];
  private readonly broken = new Set<string>();
  private readonly floatTargets: boolean;
  private lost = false;
  private destroyed = false;

  private constructor(
    private readonly renderer: WebGLRenderer,
    private readonly canvas: HTMLCanvasElement,
    private readonly options: GpuCompositorOptions,
  ) {
    const kbProgram = GlProgram.from({ vertex: PASS_VERTEX, fragment: KB_FRAGMENT, name: "sge-ken-burns", preferredFragmentPrecision: "highp" });
    const kbUniforms = (): UniformGroup =>
      new UniformGroup({ uStep: { value: new Float32Array([0, 0]), type: "vec2<f32>" }, uLod: { value: 0, type: "f32" } });
    this.kbUniforms = [kbUniforms(), kbUniforms()];
    const kbShader = (uniforms: UniformGroup): Shader => new Shader({ glProgram: kbProgram, resources: { uTexture: Texture.EMPTY.source, kb: uniforms } });
    this.kbShaders = [kbShader(this.kbUniforms[0]), kbShader(this.kbUniforms[1])];
    this.kbMeshes = [this.kbMesh(this.kbShaders[0]), this.kbMesh(this.kbShaders[1])];
    this.passGeometry = quadGeometry();
    this.composite = new Mesh<MeshGeometry, Shader>({ geometry: quadGeometry(), shader: null as unknown as Shader });
    this.composite.state.blend = false;
    this.passMesh = new Mesh<MeshGeometry, Shader>({ geometry: this.passGeometry, shader: null as unknown as Shader });
    this.passMesh.state.blend = false;
    const gl = renderer.gl as WebGL2RenderingContext;
    this.floatTargets = Boolean(gl.getExtension("EXT_color_buffer_float"));
    canvas.addEventListener("webglcontextlost", this.handleLost);
    canvas.addEventListener("webglcontextrestored", this.handleRestored);
  }

  /** A WebGL2 compositor on `canvas`, or an error when the browser or driver cannot give one. */
  static async create(canvas: HTMLCanvasElement, width: number, height: number, options: GpuCompositorOptions): Promise<GpuCompositor> {
    const renderer = new WebGLRenderer();
    await renderer.init({
      canvas,
      width: Math.max(2, Math.round(width)),
      height: Math.max(2, Math.round(height)),
      resolution: 1,
      autoDensity: false,
      antialias: false,
      backgroundColor: 0x000000,
      backgroundAlpha: 1,
      preferWebGLVersion: 2,
      powerPreference: "high-performance",
      premultipliedAlpha: true,
      preserveDrawingBuffer: true,
      clearBeforeRender: true,
      hello: false,
      accessibilityOptions: { enabledByDefault: false, activateOnTab: false, deactivateOnMouseMove: true },
      eventFeatures: { move: false, globalMove: false, click: false, wheel: false },
    } as unknown as Parameters<WebGLRenderer["init"]>[0]);
    const version = (renderer as unknown as { context?: { webGLVersion?: number } }).context?.webGLVersion;
    if (version !== 2) {
      renderer.destroy();
      throw new Error("WebGL2 is not available");
    }
    const compositor = new GpuCompositor(renderer, canvas, options);
    compositor.resize(width, height);
    return compositor;
  }

  /**
   * A scene's camera window as a quad whose shader reads the picture the way the renderer's
   * resize does (an area-like 2 x 2 supersample of each output pixel, see `KB_FRAGMENT`), not with
   * the GPU's trilinear mipmap blend, which is visibly softer between 1x and 2x minification.
   */
  private kbMesh(shader: Shader): Mesh<MeshGeometry, Shader> {
    return new Mesh<MeshGeometry, Shader>({ geometry: quadGeometry(), shader });
  }

  private readonly handleLost = (): void => {
    this.lost = true;
    this.options.onContextLost();
  };

  private readonly handleRestored = (): void => {
    this.lost = false;
    this.options.onContextRestored();
  };

  get contextLost(): boolean {
    return this.lost;
  }

  resize(width: number, height: number): void {
    const w = Math.max(2, Math.round(width));
    const h = Math.max(2, Math.round(height));
    if (w === this.width && h === this.height) return;
    this.width = w;
    this.height = h;
    this.renderer.resize(w, h, 1);
    this.unbindTargets();
    for (const target of this.targets.values()) target.destroy(true);
    this.targets.clear();
    for (const mesh of this.kbMeshes) setQuad(mesh.geometry as MeshGeometry, w, h);
    setQuad(this.composite.geometry as MeshGeometry, w, h);
  }

  /** Points every transition and pass shader away from the render targets before they go. */
  private unbindTargets(): void {
    const empty = Texture.EMPTY.source;
    const reset = (shader: Shader | null) => {
      if (!shader) return;
      const resources = shader.resources as Record<string, unknown>;
      // PixiJS defines the resource accessors as non-enumerable properties (Object.keys sees none)
      for (const name of Object.getOwnPropertyNames(resources)) if (resources[name] instanceof TextureSource) resources[name] = empty;
    };
    for (const program of this.programs.values()) {
      reset(program.shader);
      reset(program.pre);
    }
    for (const pass of this.passes.values()) reset(pass.shader);
  }

  simplifies(): boolean {
    return false;
  }

  prepare(transitionId: string): void {
    const resolved = resolveTransition(transitionId);
    this.program(resolved.filter);
  }

  /**
   * Compiles and links a transition's programs (and the blur or morph passes it runs) now. PixiJS
   * links a program the first time it draws with it, and an ANGLE/D3D11 link can take 100 to 500
   * ms: on the first frame of a cut that is a visible stall while playing.
   */
  warm(transitionId: string): void {
    if (this.destroyed || this.lost) return;
    if (!transitionId) {
      this.warmBatch();
      return;
    }
    const filter = resolveTransition(transitionId).filter;
    const shaders = this.renderer.shader as unknown as { _getProgramData?: (program: GlProgram) => unknown };
    if (typeof shaders._getProgramData !== "function") return;
    const compile = (program: GlProgram) => {
      try {
        shaders._getProgramData!(program);
      } catch {
        /* it is compiled when first drawn instead */
      }
    };
    const program = this.program(filter);
    compile(program.shader.glProgram);
    if (program.pre) compile(program.pre.glProgram);
    if (filter.blur) {
      compile(this.downsamplePass().shader.glProgram);
      compile(this.blurPass().shader.glProgram);
    }
    if (filter.morph) {
      compile(this.lumaPass().shader.glProgram);
      compile(this.blurPass().shader.glProgram);
      compile(this.fieldPass().shader.glProgram);
    }
  }

  /**
   * Links PixiJS's sprite batch shader (popups, captions and the end fade are sprites) by drawing
   * two sprites with two textures into a tiny target; otherwise the first frame with a caption
   * or a popup waits for the link (up to 400 ms measured on a seek).
   */
  private warmBatch(): void {
    try {
      const target = this.target("warm", 2, 2);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 2;
      const texture = new Texture({ source: new CanvasSource({ resource: canvas }) });
      const holder = new Container();
      for (const source of [Texture.WHITE, texture]) {
        const sprite = new Sprite(source);
        sprite.width = sprite.height = 2;
        holder.addChild(sprite);
      }
      this.renderer.render({ container: holder, target, clear: true });
      holder.destroy({ children: true });
      this.retired.push(texture);
    } catch {
      /* linked when the first sprite is drawn instead */
    }
  }

  preload(picture: Picture): void {
    if (this.destroyed || this.lost) return;
    try {
      this.renderer.texture.initSource(this.pictureTexture(picture).source);
    } catch {
      /* uploaded on the first frame that shows it */
    }
  }

  // Textures --------------------------------------------------------------------------------

  private pictureTexture(picture: Picture): Texture {
    const held = this.pictures.get(picture.path);
    if (held && held.version === picture.version) return held.texture;
    if (held) this.dropTexture(held.texture);
    const source = new ImageSource({
      resource: picture.bitmap,
      autoGenerateMipmaps: true,
      scaleMode: "linear",
      mipmapFilter: "linear",
      addressMode: "clamp-to-edge",
      alphaMode: "premultiplied-alpha",
    });
    const texture = new Texture({ source });
    this.pictures.set(picture.path, { texture, version: picture.version });
    return texture;
  }

  release(paths: Iterable<string>): void {
    for (const path of paths) {
      const held = this.pictures.get(path);
      if (!held) continue;
      this.dropTexture(held.texture);
      this.pictures.delete(path);
    }
  }

  /**
   * Destroys a picture texture once no mesh holds it any more: a mesh keeps the texture of the
   * last frame it drew, and destroying a texture its shader is still bound to makes PixiJS warn
   * ("a 'textureSource' was destroyed while still bound to a shader") on every seek.
   */
  private dropTexture(texture: Texture): void {
    for (const shader of this.kbShaders) {
      if (shader.resources.uTexture === texture.source) shader.resources.uTexture = Texture.EMPTY.source;
    }
    for (const mesh of [...this.parallaxMeshes, ...this.frontMeshes]) {
      if (mesh && mesh.texture === texture) mesh.texture = Texture.EMPTY;
    }
    texture.destroy(true);
  }

  /** The slot's slate texture: made once per canvas, uploaded again when the engine redrew it. */
  private slateTexture(slot: 0 | 1, image: SlateImage): Texture {
    let held = this.slates[slot];
    if (held && held.canvas !== image.canvas) {
      this.slateSprites[slot].texture = Texture.EMPTY;
      this.retired.push(held.texture);
      while (this.retired.length > OVERLAY_RETIRED_MAX) this.retired.shift()!.destroy(true);
      held = null;
    }
    if (!held) {
      held = { canvas: image.canvas, texture: new Texture({ source: new CanvasSource({ resource: image.canvas as HTMLCanvasElement }) }), version: image.version };
      this.slates[slot] = held;
    } else if (held.version !== image.version) {
      held.texture.source.update();
      held.version = image.version;
    }
    return held.texture;
  }

  private target(key: string, width: number, height: number, format: "rgba8unorm" | "rgba16float" = "rgba8unorm", mirror = true): RT {
    const w = Math.max(1, Math.round(width));
    const h = Math.max(1, Math.round(height));
    const name = `${key}:${w}x${h}:${format}`;
    let target = this.targets.get(name);
    if (!target) {
      target = RenderTexture.create({
        width: w,
        height: h,
        resolution: 1,
        format,
        scaleMode: "linear",
        addressMode: mirror ? "mirror-repeat" : "clamp-to-edge",
        antialias: false,
      });
      this.targets.set(name, target);
    }
    return target;
  }

  // Programs ----------------------------------------------------------------------------------

  private program(filter: TransitionFilter) {
    let program = this.programs.get(filter.id);
    if (program) return program;
    const glProgram = GlProgram.from({ vertex: PASS_VERTEX, fragment: fragmentSource(filter), name: `sge-${filter.id}`, preferredFragmentPrecision: "highp" });
    const common = new UniformGroup({
      uSize: { value: new Float32Array([this.width, this.height]), type: "vec2<f32>" },
      uT: { value: 0, type: "f32" },
      uTRaw: { value: 0, type: "f32" },
      uSeed: { value: 0, type: "u32" },
    });
    const params = new UniformGroup(
      Object.fromEntries(Object.entries(filter.uniforms).map(([name, spec]) => [name, { value: defaultValue(spec), type: spec.type, ...(spec.size ? { size: spec.size } : {}) }])) as never,
    );
    const resources: Record<string, unknown> = { uA: Texture.EMPTY.source, uB: Texture.EMPTY.source, common, params };
    if (filter.morph) resources.uField = Texture.EMPTY.source;
    if (filter.prePass) resources.uPass = Texture.EMPTY.source;
    const shader = new Shader({ glProgram, resources });
    let pre: Shader | null = null;
    if (filter.prePass) {
      const preProgram = GlProgram.from({ vertex: PASS_VERTEX, fragment: fragmentSource(filter, "pre"), name: `sge-${filter.id}-pre`, preferredFragmentPrecision: "highp" });
      pre = new Shader({ glProgram: preProgram, resources: { uA: Texture.EMPTY.source, uB: Texture.EMPTY.source, common, params } });
    }
    program = { shader, pre, common, params, filter };
    this.programs.set(filter.id, program);
    return program;
  }

  private pass(name: string, fragment: string, uniforms: Record<string, { value: unknown; type: string }>, samplers: string[]): PassProgram {
    let pass = this.passes.get(name);
    if (pass) return pass;
    const glProgram = GlProgram.from({ vertex: PASS_VERTEX, fragment, name: `sge-${name}`, preferredFragmentPrecision: "highp" });
    const group = new UniformGroup(uniforms as never);
    const resources: Record<string, unknown> = { values: group };
    for (const sampler of samplers) resources[sampler] = Texture.EMPTY.source;
    pass = { shader: new Shader({ glProgram, resources }), uniforms: group };
    this.passes.set(name, pass);
    return pass;
  }

  private runPass(pass: PassProgram, target: RT): void {
    setQuad(this.passGeometry, target.width, target.height);
    this.passMesh.shader = pass.shader;
    this.renderer.render({ container: this.passMesh, target, clear: true, clearColor: [0, 0, 0, 1] });
  }

  // Scenes ----------------------------------------------------------------------------------

  /** The display object of one scene frame (KB quad, parallax mesh, slate or nothing). */
  private sceneObject(slot: 0 | 1, ref: SceneRef, input: DrawInput): Container | null {
    const source: SceneSource = input.source(ref.scene);
    if (source.missing) {
      const sprite = this.slateSprites[slot];
      sprite.texture = this.slateTexture(slot, input.slate(ref.scene.number, slot));
      sprite.width = this.width;
      sprite.height = this.height;
      return sprite;
    }
    const picture = source.picture;
    if (!picture) return null;
    const texture = this.pictureTexture(picture);
    const imageW = picture.imageWidth;
    const imageH = picture.imageHeight;
    if (source.parallax && ref.camera) {
      const plan = source.parallax;
      const plates = plan.analysis.alpha && plan.plates ? this.plateTexturesOf(plan) : null;
      const pictureRect: Rect = [0, 0, imageW, imageH];
      const back = this.layerMesh(slot, "back", plan, ref, plates ? plan.crop : pictureRect, plates ? plates.back : texture);
      if (!plates) return back;
      const front = this.layerMesh(slot, "front", plan, ref, plan.crop, plates.front);
      const holder = this.layerHolders[slot];
      holder.removeChildren();
      holder.addChild(back, front);
      return holder;
    }
    const mesh = this.kbMeshes[slot];
    const [x, y, w, h] = ref.rect;
    const u0 = x / imageW;
    const v0 = y / imageH;
    const u1 = (x + w) / imageW;
    const v1 = (y + h) / imageH;
    const geometry = mesh.geometry as MeshGeometry;
    geometry.uvs = new Float32Array([u0, v0, u1, v0, u1, v1, u0, v1]);
    this.kbShaders[slot].resources.uTexture = texture.source;
    const sampling = kenBurnsSampling([u1 - u0, v1 - v0], [this.width, this.height], [texture.source.pixelWidth, texture.source.pixelHeight]);
    const uniforms = this.kbUniforms[slot].uniforms as { uStep: Float32Array; uLod: number };
    uniforms.uStep.set(sampling.step);
    uniforms.uLod = sampling.lod;
    return mesh;
  }

  private plateTexturesOf(plan: ParallaxPlan): { back: Texture; front: Texture } | null {
    let held = this.plateTextures.get(plan) ?? null;
    if (!held && plan.plates) {
      const make = (canvas: HTMLCanvasElement | OffscreenCanvas) =>
        new Texture({ source: new CanvasSource({ resource: canvas as HTMLCanvasElement, autoGenerateMipmaps: true, scaleMode: "linear", mipmapFilter: "linear", addressMode: "clamp-to-edge" }) });
      held = { back: make(plan.plates.back), front: make(plan.plates.front) };
      this.plateTextures.set(plan, held);
    }
    return held;
  }

  /** Uploads a plan's plates that the parallax worker has just made (before a frame shows them). */
  preloadPlates(plan: ParallaxPlan): void {
    if (this.destroyed || this.lost || !plan.plates) return;
    try {
      const held = this.plateTexturesOf(plan);
      if (!held) return;
      this.renderer.texture.initSource(held.back.source);
      this.renderer.texture.initSource(held.front.source);
    } catch {
      /* uploaded on the first frame that shows them */
    }
  }

  /** Destroys a plan's plate textures (its scene left the scenes around the playhead). */
  releasePlates(plan: ParallaxPlan): void {
    const held = this.plateTextures.get(plan);
    if (!held) return;
    this.plateTextures.delete(plan);
    this.dropTexture(held.back);
    this.dropTexture(held.front);
  }

  /** One layer's mesh of a parallax scene (the back plate or the picture, or the front objects). */
  private layerMesh(slot: 0 | 1, layer: "back" | "front", plan: ParallaxPlan, ref: SceneRef, textureRect: Rect, texture: Texture): Mesh {
    const frames = layer === "back" ? this.parallaxFrames : this.frontFrames;
    const meshes = layer === "back" ? this.parallaxMeshes : this.frontMeshes;
    const frame = parallaxMesh(plan, ref.rect, ref.camera!, layer, textureRect, [this.width, this.height], undefined, undefined, frames[slot]);
    frames[slot] = frame;
    let mesh = meshes[slot];
    if (!mesh) {
      mesh = new Mesh({ geometry: new MeshGeometry({ positions: frame.positions, uvs: frame.uvs, indices: frame.indices }), texture });
      meshes[slot] = mesh;
    } else {
      const geometry = mesh.geometry as MeshGeometry;
      geometry.positions = frame.positions;
      geometry.uvs = frame.uvs;
      if (geometry.indices !== frame.indices) geometry.indices = frame.indices;
      mesh.texture = texture;
    }
    return mesh;
  }

  private renderScene(slot: 0 | 1, ref: SceneRef, input: DrawInput, target: RT): void {
    const holder = this.holders[slot];
    holder.removeChildren();
    const object = this.sceneObject(slot, ref, input);
    if (object) holder.addChild(object);
    this.renderer.render({ container: holder, target, clear: true, clearColor: [0, 0, 0, 1] });
  }

  // Blurs and the morph field ---------------------------------------------------------------------

  private downsamplePass(): PassProgram {
    return this.pass("downsample", DOWNSAMPLE_FRAGMENT, {
      uSourceSize: { value: new Float32Array(2), type: "vec2<f32>" },
      uFactor: { value: 1, type: "i32" },
    }, ["uSource"]);
  }

  private blurPass(): PassProgram {
    return this.pass("blur", BLUR_FRAGMENT, {
      uSourceSize: { value: new Float32Array(2), type: "vec2<f32>" },
      uDirection: { value: new Float32Array(2), type: "vec2<f32>" },
      uSigma: { value: 1, type: "f32" },
      uRadius: { value: 1, type: "i32" },
    }, ["uSource"]);
  }

  private lumaPass(): PassProgram {
    return this.pass("luma", LUMA_FRAGMENT, {
      uSourceSize: { value: new Float32Array(2), type: "vec2<f32>" },
      uTargetSize: { value: new Float32Array(2), type: "vec2<f32>" },
    }, ["uA", "uB"]);
  }

  private fieldPass(): PassProgram {
    return this.pass("field", FIELD_FRAGMENT, {
      uSmallSize: { value: new Float32Array(2), type: "vec2<f32>" },
      uEps: { value: 0.02, type: "f32" },
    }, ["uLuma"]);
  }

  private gaussian(source: RT, sigma: number, key: string): RT {
    const plan = blurPlan(sigma);
    if (!plan) return source;
    let current = source;
    let width = this.width;
    let height = this.height;
    if (plan.factor > 1) {
      width = Math.max(1, Math.round(this.width / plan.factor));
      height = Math.max(1, Math.round(this.height / plan.factor));
      const small = this.target(`${key}-small`, width, height);
      const down = this.downsamplePass();
      (down.uniforms.uniforms.uSourceSize as Float32Array).set([this.width, this.height]);
      down.uniforms.uniforms.uFactor = plan.factor;
      down.shader.resources.uSource = current.source;
      this.runPass(down, small);
      current = small;
    }
    const blur = this.blurPass();
    const values = blur.uniforms.uniforms;
    (values.uSourceSize as Float32Array).set([width, height]);
    values.uSigma = plan.sigma;
    values.uRadius = plan.radius;
    const across = this.target(`${key}-h`, width, height);
    (values.uDirection as Float32Array).set([1, 0]);
    blur.shader.resources.uSource = current.source;
    this.runPass(blur, across);
    const down = this.target(`${key}-v`, width, height);
    (values.uDirection as Float32Array).set([0, 1]);
    blur.shader.resources.uSource = across.source;
    this.runPass(blur, down);
    return down;
  }

  private morphField(a: RT, b: RT, frame: TransitionFrame, filter: TransitionFilter): RT | null {
    const terms = filter.morph?.(frame);
    if (!terms) return null;
    const [sw, sh] = terms.small;
    const format = this.floatTargets ? "rgba16float" : "rgba8unorm";
    const luma = this.lumaPass();
    (luma.uniforms.uniforms.uSourceSize as Float32Array).set([this.width, this.height]);
    (luma.uniforms.uniforms.uTargetSize as Float32Array).set([sw, sh]);
    luma.shader.resources.uA = a.source;
    luma.shader.resources.uB = b.source;
    const lumaTarget = this.target("morph-luma", sw, sh, format);
    this.runPass(luma, lumaTarget);
    let smooth = lumaTarget;
    if (terms.sigmaSmall >= 0.35) {
      const blur = this.blurPass();
      const values = blur.uniforms.uniforms;
      (values.uSourceSize as Float32Array).set([sw, sh]);
      values.uSigma = terms.sigmaSmall;
      values.uRadius = kernelRadius(terms.sigmaSmall, true);
      const across = this.target("morph-h", sw, sh, format);
      (values.uDirection as Float32Array).set([1, 0]);
      blur.shader.resources.uSource = lumaTarget.source;
      this.runPass(blur, across);
      smooth = this.target("morph-v", sw, sh, format);
      (values.uDirection as Float32Array).set([0, 1]);
      blur.shader.resources.uSource = across.source;
      this.runPass(blur, smooth);
    }
    const field = this.fieldPass();
    (field.uniforms.uniforms.uSmallSize as Float32Array).set([sw, sh]);
    field.uniforms.uniforms.uEps = terms.eps;
    field.shader.resources.uLuma = smooth.source;
    const out = this.target("morph-field", sw, sh, format, false);
    this.runPass(field, out);
    return out;
  }

  // Frames ----------------------------------------------------------------------------------

  draw(input: DrawInput): void {
    if (this.destroyed || this.lost) return;
    this.resize(input.width, input.height);
    const state = input.state;
    let main: Container | null = null;
    let fadeAlpha = 0;
    let fadeColour: [number, number, number] = [0, 0, 0];
    if (state.kind === "scene") {
      main = this.sceneObject(0, state.a, input);
    } else if (state.kind === "fade") {
      main = this.sceneObject(0, state.a, input);
      fadeAlpha = Math.min(Math.max(state.weight, 0), 1);
      fadeColour = state.colour;
    } else if (state.kind === "cut") {
      if (state.t <= 0) main = this.sceneObject(0, state.a, input);
      else if (state.t >= 1) main = this.sceneObject(1, state.b, input);
      else main = this.drawCut(state, input);
    }
    this.stage.removeChildren();
    if (main) this.stage.addChild(main);
    if (fadeAlpha > 0) {
      this.fade.width = this.width;
      this.fade.height = this.height;
      this.fade.tint = (Math.round(fadeColour[0] * 255) << 16) | (Math.round(fadeColour[1] * 255) << 8) | Math.round(fadeColour[2] * 255);
      this.fade.alpha = fadeAlpha;
      this.stage.addChild(this.fade);
    }
    this.placeOverlays(input.overlays);
    this.renderer.render({ container: this.stage, clear: true, clearColor: [0, 0, 0, 1] });
  }

  private drawCut(state: Extract<FrameState, { kind: "cut" }>, input: DrawInput): Container | null {
    const transition = state.cut.transition!;
    let filter = transition.filter;
    if (this.broken.has(filter.id)) filter = TRANSITION_FILTERS.film_dissolve;
    const frame: TransitionFrame = {
      width: this.width,
      height: this.height,
      t: state.t,
      tRaw: state.tRaw,
      durationS: state.cut.durationS,
      fps: state.cut.frames / Math.max(state.cut.durationS, 1e-9),
      easing: transition.easing,
      params: transition.params,
      seed: state.cut.seed,
    };
    const rtA = this.target("A", this.width, this.height);
    const rtB = this.target("B", this.width, this.height);
    this.renderScene(0, state.a, input, rtA);
    this.renderScene(1, state.b, input, rtB);
    let sourceA: RT = rtA;
    let sourceB: RT = rtB;
    if (filter.blur) {
      const blur = filter.blur(frame);
      sourceA = this.gaussian(rtA, blur.a, "blurA");
      sourceB = this.gaussian(rtB, blur.b, "blurB");
    }
    const program = this.program(filter);
    try {
      const common = program.common.uniforms;
      (common.uSize as Float32Array).set([this.width, this.height]);
      common.uT = state.t;
      common.uTRaw = state.tRaw;
      common.uSeed = Math.max(0, Math.trunc(state.cut.seed)) >>> 0;
      const values = filter.values(frame);
      const params = program.params.uniforms as Record<string, unknown>;
      for (const [name, value] of Object.entries(values)) {
        const current = params[name];
        if (typeof value === "number") params[name] = value;
        else if (current instanceof Float32Array) current.set(value.slice(0, current.length));
      }
      program.shader.resources.uA = sourceA.source;
      program.shader.resources.uB = sourceB.source;
      if (program.pre) {
        program.pre.resources.uA = sourceA.source;
        program.pre.resources.uB = sourceB.source;
        const sharp = this.target("pass", this.width, this.height);
        setQuad(this.passGeometry, this.width, this.height);
        this.passMesh.shader = program.pre;
        this.renderer.render({ container: this.passMesh, target: sharp, clear: true, clearColor: [0, 0, 0, 1] });
        program.shader.resources.uPass = sharp.source;
      }
      if (filter.morph) {
        const field = this.morphField(rtA, rtB, frame, filter);
        if (field) program.shader.resources.uField = field.source;
      }
      this.composite.shader = program.shader;
      return this.composite;
    } catch (error) {
      this.broken.add(filter.id);
      this.options.onError(`The preview could not draw the ${transition.spec.label.toLowerCase()} transition; it shows a dissolve there instead.`);
      void error;
      return this.sceneObject(0, state.a, input);
    }
  }

  /**
   * Takes an overlay's texture out of use. A popup or caption sprite was drawn through PixiJS's
   * batcher, whose cached bind groups still hold its texture for a while; destroying it at once
   * makes PixiJS warn ("a 'textureSource' was destroyed while still bound to a shader"). The
   * texture is kept until more than OVERLAY_RETIRED_MAX have been put aside (popups are small).
   */
  private retire(held: { sprite: Sprite; texture: Texture }): void {
    held.sprite.removeFromParent();
    held.sprite.destroy();
    this.retired.push(held.texture);
    while (this.retired.length > OVERLAY_RETIRED_MAX) this.retired.shift()!.destroy(true);
  }

  private placeOverlays(overlays: readonly Overlay[]): void {
    const seen = new Set<string>();
    const clock = ++this.overlayClock;
    for (const overlay of overlays) {
      seen.add(overlay.key);
      let held = this.overlaySprites.get(overlay.key);
      if (held && held.image !== overlay.image) {
        this.retire(held);
        this.overlaySprites.delete(overlay.key);
        held = undefined;
      }
      if (!held) {
        const isBitmap = typeof ImageBitmap !== "undefined" && overlay.image instanceof ImageBitmap;
        const source = isBitmap
          ? new ImageSource({ resource: overlay.image, alphaMode: overlay.premultiplied ? "premultiplied-alpha" : "premultiply-alpha-on-upload", scaleMode: "linear", autoGenerateMipmaps: true, mipmapFilter: "linear" })
          : new CanvasSource({ resource: overlay.image as HTMLCanvasElement, scaleMode: "linear", autoGenerateMipmaps: true, mipmapFilter: "linear" });
        const texture = new Texture({ source });
        held = { sprite: new Sprite(texture), texture, version: overlay.version, image: overlay.image, used: clock };
        this.overlaySprites.set(overlay.key, held);
      } else if (held.version !== overlay.version) {
        held.texture.source.update();
        held.version = overlay.version;
      }
      held.used = clock;
      const sprite = held.sprite;
      sprite.x = overlay.x;
      sprite.y = overlay.y;
      sprite.width = overlay.w;
      sprite.height = overlay.h;
      sprite.alpha = overlay.alpha;
      this.stage.addChild(sprite);
    }
    // popups that went off screen keep their sprite (they often come back on a seek); beyond
    // OVERLAY_KEEP the ones unused the longest are put aside
    if (this.overlaySprites.size > OVERLAY_KEEP) {
      const idle = [...this.overlaySprites.entries()].filter(([key]) => !seen.has(key)).sort((a, b) => a[1].used - b[1].used);
      for (const [key, held] of idle.slice(0, this.overlaySprites.size - OVERLAY_KEEP)) {
        this.retire(held);
        this.overlaySprites.delete(key);
      }
    }
  }

  /** Reads back the frame on screen (tests and the parity harness). */
  snapshot(): ImageData | null {
    const gl = this.renderer.gl as WebGL2RenderingContext;
    const pixels = new Uint8ClampedArray(this.width * this.height * 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.readPixels(0, 0, this.width, this.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    // WebGL rows run bottom to top
    const row = this.width * 4;
    const flipped = new Uint8ClampedArray(pixels.length);
    for (let y = 0; y < this.height; y++) flipped.set(pixels.subarray((this.height - 1 - y) * row, (this.height - y) * row), y * row);
    return new ImageData(flipped, this.width, this.height);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.canvas.removeEventListener("webglcontextlost", this.handleLost);
    this.canvas.removeEventListener("webglcontextrestored", this.handleRestored);
    // The shaders go first, so none of their bind groups is still listening to a texture when
    // the GPU objects are freed (PixiJS would warn once per texture); then the renderer, which
    // frees the GPU side of every texture and render target it made. The JS side goes with this
    // object (the pictures' bitmaps are closed by the picture store).
    const shaders = [
      ...this.kbShaders,
      ...[...this.programs.values()].flatMap((program) => [program.shader, program.pre]),
      ...[...this.passes.values()].map((pass) => pass.shader),
    ];
    for (const shader of shaders) {
      try {
        shader?.destroy();
      } catch {
        /* already gone */
      }
    }
    try {
      this.renderer.destroy();
    } catch {
      /* the context may be gone already */
    }
    this.targets.clear();
    this.pictures.clear();
    this.plateTextures.clear();
    this.slates[0] = this.slates[1] = null;
    this.overlaySprites.clear();
    this.retired.length = 0;
    this.programs.clear();
    this.passes.clear();
  }
}
