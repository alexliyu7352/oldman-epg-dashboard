import "@app/css/app.css";
import { createHttpClient, createI18n, createOldmanContext, setOldmanContext, startOldman, type HttpClientOptions, type LanguageDefinition, type OldmanApp } from "oldman-web/core";
import { defaultLanguage, languageAliases, languageDefinitions } from "./i18n/generated";

const pageEntries = import.meta.glob([
  "./pages/backend.ts",
  "./pages/examples.ts",
  "./pages/login.ts",
  "!./pages/**/*.test.ts"
]);

let oldmanAppPromise: Promise<OldmanApp> | null = null;

/**
 * 页面入口注册完成后启动共享 Oldman 运行时。
 */
export async function startOldmanApp(): Promise<OldmanApp> {
  if (oldmanAppPromise) return oldmanAppPromise;

  oldmanAppPromise = (async () => {
    await Promise.resolve();
    const httpOptions: HttpClientOptions = {};
    const http = createHttpClient(httpOptions);
    const assetBaseUrl = readAssetBaseUrl();
    const i18n = createI18n({
      aliases: languageAliases,
      catalogLoader: (language) => loadLanguageCatalog(language, assetBaseUrl),
      defaultLanguage,
      document,
      http,
      languages: languageDefinitions
    });
    const context = createOldmanContext({ assetBaseUrl, document, http, httpOptions, i18n });
    setOldmanContext(context);
    await context.i18n.init();
    const app = await startOldman({ context, pageLoader: loadPageEntry });
    document.documentElement.dataset.omReady = "true";
    return app;
  })();

  try {
    return await oldmanAppPromise;
  } catch (error) {
    oldmanAppPromise = null;
    throw error;
  }
}

/**
 * 停止共享 Oldman 运行时，供测试和整页销毁流程使用。
 */
export async function stopOldmanApp(): Promise<void> {
  if (!oldmanAppPromise) return;

  const app = await oldmanAppPromise;
  await app.destroy();
  oldmanAppPromise = null;
  delete document.documentElement.dataset.omReady;
}

void startOldmanApp().catch((error: unknown) => {
  console.error("Oldman startup failed", error);
});

/**
 * 当 Turbo 渲染未注册页面时，动态导入对应页面入口。
 */
async function loadPageEntry(pageName: string): Promise<void> {
  const loader = pageEntries[`./pages/${pageName}.ts`];
  if (!loader) return;

  await loader();
}

function readAssetBaseUrl(): string {
  const configured = document.querySelector<HTMLMetaElement>('meta[name="oldman-asset-base"]')?.content;
  if (configured) return new URL(configured, window.location.href).toString();

  return `${window.location.origin}/`;
}

async function loadLanguageCatalog(language: LanguageDefinition, assetBaseUrl: string) {
  const url = new URL(language.catalogPath.replace(/^\/+/, ""), assetBaseUrl).toString();
  const response = await fetch(url, {
    headers: {
      Accept: "application/json"
    }
  });
  if (!response.ok) {
    return {
      locale: language.locale,
      messages: {}
    };
  }
  return response.json();
}
