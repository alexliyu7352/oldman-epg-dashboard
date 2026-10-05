import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

const rootDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(rootDir, "..");
const localOldmanRoot = resolve(projectRoot, ".local/oldman");
const staticDistBase = "/static/dist/";

/** The demo key. A real deployment passes its own and keeps it out of the repo. */
const DEMO_FINGERPRINT_KEY = "b2xkbWFuLWVwZy1kZW1vLWZpbmdlcnByaW50LWtleSE=";

/**
 * 把指纹密钥在构建期拆成两段，运行时异或还原。
 *
 * 密钥在构建期进包，不从页面下发——每个部署一把，而框架的 bundle 是共用的。拆两段是
 * 为了让静态 grep 在产物里找不到一段连续的密钥；这只提高成本，拿到 bundle 跑一遍就能
 * 还原，页面上也是这么写的。拆分是确定性的（由密钥自身派生），构建保持可重现。
 */
function fingerprintKeyShares(): { s1: number[]; s2: number[] } {
  const raw = Buffer.from(process.env.OLDMAN_FINGERPRINT_KEY?.trim() || DEMO_FINGERPRINT_KEY, "base64");
  const mask = createHash("sha256").update(raw).update("oldman-fingerprint-split").digest();
  return {
    s1: Array.from(mask.subarray(0, raw.length)),
    s2: Array.from(raw.map((byte, index) => byte ^ mask[index]!))
  };
}

export default defineConfig(({ command }) => ({
  base: command === "build" ? staticDistBase : "/",
  plugins: [tailwindcss()],
  define: {
    global: "globalThis",
    __OLDMAN_FINGERPRINT_SHARES__: JSON.stringify(fingerprintKeyShares())
  },
  resolve: {
    alias: [
      ...localOldmanWebAliases(),
      { find: "@app", replacement: resolve(rootDir, "src") }
    ],
    dedupe: localOldmanWebDependencies()
  },
  build: {
    emptyOutDir: true,
    manifest: true,
    outDir: resolve(rootDir, "../static/dist"),
    // ApexCharts alone is about 580 kB; oldman-web loads it only on pages that have a chart.
    // Any other chunk past 600 kB still gets Vite's warning.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      input: {
        main: resolve(rootDir, "src/main.ts")
      },
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/simplebar") || id.includes("node_modules/node-waves")) {
            return "vendor-ui";
          }
          return undefined;
        }
      }
    }
  },
  server: {
    origin: "http://localhost:5173",
    port: 5173,
    strictPort: true
  },
  test: {
    environment: "jsdom",
    fileParallelism: false,
    include: ["src/**/*.test.ts"]
  }
}));

/** Resolve a sibling Oldman checkout to TypeScript source without changing package manifests. */
function localOldmanWebAliases(): Array<{ find: RegExp; replacement: string }> {
  const packageRoot = resolve(localOldmanRoot, "frontend/packages/oldman-web");
  const packageFile = resolve(packageRoot, "package.json");
  if (!existsSync(packageFile)) return [];

  const manifest = JSON.parse(readFileSync(packageFile, "utf8")) as {
    exports?: Record<string, string | { import?: string }>;
  };
  return Object.entries(manifest.exports ?? {}).flatMap(([subpath, target]) => {
    const importTarget = typeof target === "string" ? target : target.import;
    if (!importTarget) return [];
    const specifier = subpath === "." ? "oldman-web" : `oldman-web/${subpath.slice(2)}`;
    const sourceTarget = subpath === "./package.json"
      ? importTarget
      : importTarget.replace("./dist/", "./src/").replace(/\.js$/, ".ts");
    return [{
      find: new RegExp(`^${specifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`),
      replacement: resolve(packageRoot, sourceTarget)
    }];
  });
}

/** Keep source imports on the Demo's dependency instances during local development. */
function localOldmanWebDependencies(): string[] {
  const packageFile = resolve(localOldmanRoot, "frontend/packages/oldman-web/package.json");
  if (!existsSync(packageFile)) return [];
  const manifest = JSON.parse(readFileSync(packageFile, "utf8")) as {
    dependencies?: Record<string, string>;
  };
  const projectManifest = JSON.parse(readFileSync(resolve(rootDir, "package.json"), "utf8")) as {
    dependencies?: Record<string, string>;
  };
  const projectDependencies = projectManifest.dependencies ?? {};
  return Object.keys(manifest.dependencies ?? {}).filter((name) => name in projectDependencies);
}
