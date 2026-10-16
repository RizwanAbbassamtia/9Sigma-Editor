// Builds the parity harness page (dev only, docs/M5-CONTRACT.md section 6) with the front end's
// own Vite and node_modules. The page is served by the app under /parity/ so it can call the
// real API with the session of the signed-in test account.
//
//   SGE_PARITY_OUT=<folder> node frontend/node_modules/vite/bin/vite.js build \
//     --config tests/preview_parity/harness/vite.config.mjs
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "../../../frontend");

export default {
  root: HERE,
  base: "/parity/",
  publicDir: false,
  cacheDir: resolve(process.env.SGE_PARITY_VITE_CACHE ?? tmpdir(), "sge-parity-vite"),
  logLevel: "warn",
  resolve: {
    alias: { "@preview": resolve(FRONTEND, "src/preview") },
  },
  build: {
    outDir: process.env.SGE_PARITY_OUT ?? resolve(tmpdir(), "sge-parity-site", "parity"),
    emptyOutDir: true,
    target: "es2022",
    chunkSizeWarningLimit: 4096,
    reportCompressedSize: false,
  },
};
