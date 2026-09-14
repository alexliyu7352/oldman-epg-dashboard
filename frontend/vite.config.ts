import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import nunjucks from "nunjucks";
import { defineConfig, type Plugin } from "vitest/config";
import { dashboardPreviewContext } from "./preview/dashboard";
import { languageDefinitions, type LanguageDefinition } from "./src/i18n/generated";

const rootDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(rootDir, "..");
const templateDir = resolve(projectRoot, "templates");
const localOldmanRoot = resolve(projectRoot, ".local/oldman");
const localOldmanTemplateDir = resolve(localOldmanRoot, "oldman/web/templates");
const collectedStaticDir = process.env.OLDMAN_COLLECTED_STATIC_DIR?.trim()
  ? resolve(process.env.OLDMAN_COLLECTED_STATIC_DIR)
  : resolve(projectRoot, "static");
const staticDistBase = "/static/dist/";
const frameworkStaticUrlPrefix = "/static/oldman/";
const previewLanguageDefinitions: readonly LanguageDefinition[] = languageDefinitions;

export default defineConfig(({ command }) => ({
  base: command === "build" ? staticDistBase : "/",
  plugins: [oldmanTemplatePreview(), tailwindcss()],
  define: {
    global: "globalThis"
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

/**
 * 提供真实业务模板的 Nunjucks 预览能力，便于不启动后端时检查模板结构。
 */
function oldmanTemplatePreview(): Plugin {
  return {
    name: "oldman-template-preview",
    configureServer(server) {
      const environment = createPreviewEnvironment();
      server.middlewares.use((request, response, next) => {
        const url = request.url?.split("?")[0] ?? "";
        if (request.method === "GET" && url.startsWith(frameworkStaticUrlPrefix)) {
          const assetPath = frameworkPreviewAssetPath(url);
          if (assetPath === null) {
            response.statusCode = 400;
            response.end("Invalid Oldman static asset path");
            return;
          }
          void readFile(assetPath).then(
            (content) => {
              response.statusCode = 200;
              response.setHeader(
                "Content-Type",
                assetPath.endsWith(".svg")
                  ? "image/svg+xml; charset=utf-8"
                  : "application/octet-stream"
              );
              response.end(content);
            },
            (error: NodeJS.ErrnoException) => {
              if (error.code === "ENOENT") {
                response.statusCode = 404;
                response.end("Oldman static asset not found");
                return;
              }
              next(error);
            }
          );
          return;
        }
        if (request.method !== "GET" || !url.startsWith("/templates/") || !url.endsWith(".html")) {
          next();
          return;
        }

        const templateName = url.replace(/^\/templates\//, "");
        try {
          const html = environment.render(templateName, dashboardPreviewContext());
          response.statusCode = 200;
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end(html);
        } catch (error) {
          next(error);
        }
      });
    }
  };
}

/** Build the Nunjucks preview only when a development server needs it. */
function createPreviewEnvironment(): nunjucks.Environment {
  const environment = new nunjucks.Environment(new JinjaPreviewLoader(templateSearchPaths()), {
    autoescape: true,
    noCache: true,
    throwOnUndefined: true
  });

  environment.addGlobal("app_main_bundle", "app:main");
  environment.addGlobal("bundle_client", () => {
    return new nunjucks.runtime.SafeString('<script type="module" src="/@vite/client"></script>');
  });
  environment.addGlobal("bundle_styles", () => "");
  environment.addGlobal("bundle_modulepreload", () => "");
  environment.addGlobal("bundle_script", () => {
    return new nunjucks.runtime.SafeString('<script type="module" src="/src/main.ts"></script>');
  });
  environment.addGlobal("bundle_entry", () => {
    return new nunjucks.runtime.SafeString('<script type="module" src="/src/main.ts"></script>');
  });
  environment.addGlobal("bundle_asset_base_url", () => "/");
  environment.addGlobal("bundle_asset_url", (_bundleName: string, assetPath: string) => `/${assetPath.replace(/^\/+/, "")}`);
  environment.addGlobal("_", (message: string) => message);
  environment.addGlobal("gettext", (message: string) => message);
  environment.addGlobal("dashboard_csrf_token", () => "preview-csrf-token");
  environment.addGlobal("dashboard_current_language", () => previewLanguageDefinitions[0]?.code ?? "en");
  environment.addGlobal("dashboard_language_items", () =>
    previewLanguageDefinitions.map((language, index) => {
      const flagUrl = language.flagUrl ?? (language.flag ? `/${language.flag.replace(/^\/+/, "")}` : "");
      return {
        aliases: [...language.aliases],
        code: language.code,
        flag: language.flag,
        flag_asset: flagUrl,
        flagUrl,
        is_current: index === 0,
        locale: language.locale,
        name: language.name
      };
    })
  );
  return environment;
}

/** Translate the one Jinja tuple-default spelling that Nunjucks emits as invalid JavaScript. */
class JinjaPreviewLoader extends nunjucks.FileSystemLoader {
  override getSource(name: string): nunjucks.LoaderSource {
    const source = super.getSource(name);
    return {
      ...source,
      src: source.src.replaceAll("=()", "=[]")
    };
  }
}

/**
 * 项目模板优先，Oldman dashboard 基类从当前 wheel 或仓库源码加载。
 */
function templateSearchPaths(): string[] {
  const oldmanTemplateDir = oldmanTemplateDirectory();
  const baseTemplate = resolve(oldmanTemplateDir, "oldman/dashboard/base.html");
  if (!existsSync(baseTemplate)) {
    throw new Error(`Oldman dashboard templates were not found: ${baseTemplate}`);
  }
  return [templateDir, oldmanTemplateDir];
}

/**
 * 使用同一 Python 包定位预览所需的模板。
 */
function oldmanTemplateDirectory(): string {
  return process.env.OLDMAN_PYTHON_TEMPLATE_DIR?.trim()
    || localOldmanTemplateDir;
}

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

/**
 * 将共享静态 URL 安全映射到项目已经收集的公开目录。
 */
function frameworkPreviewAssetPath(url: string): string | null {
  let relativeAsset: string;
  try {
    relativeAsset = decodeURIComponent(
      url.slice(frameworkStaticUrlPrefix.length)
    );
  } catch {
    return null;
  }
  if (!relativeAsset || relativeAsset.includes("\0")) return null;

  const logicalPath = `oldman/${relativeAsset}`;
  const assetPath = resolve(collectedStaticDir, logicalPath);
  const containedPath = relative(collectedStaticDir, assetPath);
  if (
    !containedPath
    || containedPath === ".."
    || containedPath.startsWith(`..${sep}`)
    || isAbsolute(containedPath)
  ) {
    return null;
  }
  return assetPath;
}
