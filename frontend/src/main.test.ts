import { afterEach, describe, expect, it, vi } from "vitest";

const emptyCatalog = () => new Response(JSON.stringify({ locale: "en", messages: {} }), { headers: { "Content-Type": "application/json" } });

/** Import main.ts, which starts the dashboard, and wait until the page is mounted. */
async function startMain(): Promise<void> {
  vi.resetModules();
  await import("./main");
  await vi.waitFor(() => expect(document.documentElement.dataset.omReady).toBe("true"));
}

// What main.ts wires into the framework's startDashboard (tested in oldman-web): the fallback page and the page entries.
describe("main.ts", () => {
  afterEach(async () => {
    const { stopDashboard } = await import("oldman-web/dashboard");
    await stopDashboard();
    delete document.body.dataset.omPage;
    delete document.body.dataset.omExamplesReady;
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("mounts this project's base page for an entry name nothing registered", async () => {
    document.head.innerHTML = '<meta name="oldman-asset-base" content="http://localhost:5173/">';
    document.body.innerHTML = '<main data-om-page="never-registered"></main>';
    vi.stubGlobal("fetch", vi.fn(async () => emptyCatalog()));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await startMain();
    const { BasePage } = await import("./pages/base-page");
    const { getOldmanContext } = await import("oldman-web/core");

    expect(getOldmanContext().pageRegistry.current).toBeInstanceOf(BasePage);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("No page registered for never-registered"));
  });

  it("loads the dedicated examples page entry", async () => {
    document.head.innerHTML = '<meta name="oldman-asset-base" content="http://localhost:5173/">';
    document.body.dataset.omPage = "examples";
    document.body.innerHTML = `
      <aside data-om-sidebar></aside>
      <header id="page-topbar"></header>
      <main data-examples-shell></main>
    `;
    vi.stubGlobal("fetch", vi.fn(async () => emptyCatalog()));

    await startMain();

    expect(document.body.dataset.omExamplesReady).toBe("true");
  });
});
