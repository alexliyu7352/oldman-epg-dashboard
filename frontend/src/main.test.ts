import { describe, expect, it, vi } from "vitest";

describe("startOldmanApp", () => {
  it("starts the runtime after page entry registration and mounts the page", async () => {
    document.head.innerHTML = '<meta name="oldman-asset-base" content="http://localhost:5173/">';
    document.documentElement.removeAttribute("data-om-ready");
    document.body.innerHTML = '<main data-om-page="main-test"></main>';
    const fetch = vi.fn(async (url: RequestInfo | URL) => {
      return new Response(JSON.stringify({ locale: "en", messages: { menu: "Menu" } }), {
        headers: { "Content-Type": "application/json" }
      });
    });
    vi.stubGlobal("fetch", fetch);

    vi.resetModules();
    const { Page, setupPage } = await import("oldman-web/core");

    /**
     * 最小测试页面，用于证明 main.ts 会启动 Oldman 页面生命周期。
     */
    class MainTestPage extends Page {
      override async mount(): Promise<void> {
        this.root.dataset.mainTestMounted = "true";
      }
    }

    setupPage("main-test", MainTestPage);
    const { startOldmanApp, stopOldmanApp } = await import("./main");

    await startOldmanApp();
    expect(fetch).toHaveBeenCalledWith("http://localhost:5173/i18n/en.json", expect.any(Object));
    expect(document.documentElement.dataset.omReady).toBe("true");
    expect(document.querySelector("[data-om-page]")).not.toBeNull();
    expect(document.querySelector<HTMLElement>("[data-om-page]")?.dataset.mainTestMounted).toBe("true");

    await stopOldmanApp();
    vi.unstubAllGlobals();
  });

  it("resolves relative asset base before loading i18n catalogs", async () => {
    document.head.innerHTML = '<meta name="oldman-asset-base" content="/static/dist/">';
    document.documentElement.removeAttribute("data-om-ready");
    document.body.innerHTML = '<main data-om-page="main-test"></main>';
    const fetch = vi.fn(async () => {
      return new Response(JSON.stringify({ locale: "en", messages: {} }), {
        headers: { "Content-Type": "application/json" }
      });
    });
    vi.stubGlobal("fetch", fetch);

    vi.resetModules();
    const { Page, setupPage } = await import("oldman-web/core");

    class MainTestPage extends Page {}

    setupPage("main-test", MainTestPage);
    const { startOldmanApp, stopOldmanApp } = await import("./main");

    await startOldmanApp();
    expect(fetch).toHaveBeenCalledWith(new URL("/static/dist/i18n/en.json", window.location.href).toString(), expect.any(Object));

    await stopOldmanApp();
    vi.unstubAllGlobals();
  });

  it("loads the dedicated examples page entry", async () => {
    document.head.innerHTML = '<meta name="oldman-asset-base" content="http://localhost:5173/">';
    document.documentElement.removeAttribute("data-om-ready");
    document.body.dataset.omPage = "examples";
    document.body.innerHTML = `
      <aside data-om-sidebar></aside>
      <header id="page-topbar"></header>
      <main data-examples-shell></main>
    `;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ locale: "en", messages: {} }))));

    vi.resetModules();
    const { startOldmanApp, stopOldmanApp } = await import("./main");

    await startOldmanApp();
    expect(document.body.dataset.omExamplesReady).toBe("true");

    await stopOldmanApp();
    delete document.body.dataset.omPage;
    delete document.body.dataset.omExamplesReady;
    vi.unstubAllGlobals();
  });

});
