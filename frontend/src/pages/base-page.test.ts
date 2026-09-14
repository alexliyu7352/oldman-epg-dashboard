import { afterEach, describe, expect, it, vi } from "vitest";
import { BasePage } from "./base-page";
import { Component } from "oldman-web/core";

vi.mock("simplebar", () => ({
  default: class SimpleBarMock {
    static instances = new WeakMap<Node, SimpleBarMock>();

    static getOptions(): Record<string, unknown> {
      return {};
    }

    static removeObserver(): void {}

    static initDOMLoadedElements(): void {}

    constructor(readonly element: HTMLElement) {}

    /**
     * 匹配 ScrollArea 使用的 SimpleBar 清理方法，避免触发布局 API。
     */
    unMount(): void {}

    /**
     * 匹配 ScrollArea 对外暴露的滚动元素读取方法。
     */
    getScrollElement(): HTMLElement {
      return this.element;
    }
  }
}));

class TestBasePage extends BasePage {}

/** Direct-mount unit tests must complete all teardown stages, not only a hook. */
async function disposePage(page: BasePage): Promise<void> {
  await page.beforeUnmount();
  await page.unmount();
  await page.unmountComponents();
  await page.runCleanup();
}

class TestPageComponent extends Component {
  /**
   * 标记声明式页面组件已经挂载。
   */
  override async mount(): Promise<void> {
    this.root.dataset.testPageComponentMounted = "true";
  }
}

class InitialLoadingCompleteComponent extends Component {
  override async mount(): Promise<void> {
    const scope = this.root.closest<HTMLElement>("[data-om-loading-initial='true']");
    this.root.dataset.sawInitialLoading = scope?.dataset.omPreloaderStatus || "";
    this.root.dispatchEvent(new CustomEvent("om:component:render-complete", { bubbles: true }));
  }
}

describe("BasePage", () => {
  afterEach(() => {
    history.replaceState(null, "", "/");
    document.body.replaceChildren();
    document.body.removeAttribute("data-om-page");
    document.body.className = "";
  });

  it("clears transient overlays without resetting the desktop sidebar choice", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.classList.add("fullscreen-enable", "om-modal-open", "swal2-shown", "swal2-height-auto");
    document.body.style.overflow = "hidden";
    document.body.style.paddingRight = "17px";
    document.body.innerHTML = `
      <button id="topnav-hamburger-icon">
        <span class="hamburger-icon open"></span>
      </button>
      <div class="vertical-overlay"></div>
      <div data-om-modal-backdrop></div>
      <div class="swal2-container"><div class="swal2-popup">Blocked</div></div>
      <main class="page-content"></main>
    `;
    document.documentElement.setAttribute("data-sidebar-size", "sm");

    const page = new TestBasePage(document.body);

    await disposePage(page);

    expect(document.body.classList.contains("fullscreen-enable")).toBe(false);
    expect(document.body.classList.contains("om-modal-open")).toBe(false);
    expect(document.body.classList.contains("swal2-shown")).toBe(false);
    expect(document.body.classList.contains("swal2-height-auto")).toBe(false);
    expect(document.body.style.overflow).toBe("");
    expect(document.body.style.paddingRight).toBe("");
    expect(document.querySelector(".hamburger-icon")?.classList.contains("open")).toBe(true);
    expect(document.querySelector("[data-om-modal-backdrop]")).toBeNull();
    expect(document.querySelector(".swal2-container")).toBeNull();
    expect(document.documentElement.getAttribute("data-sidebar-size")).toBe("sm");
  });

  it("marks the current sidebar link and parent collapse active from the current path", async () => {
    history.replaceState(null, "", "/dashboard/ui-modals.html");
    document.body.dataset.omPage = "ui-modals";
    document.body.innerHTML = `
      <ul id="navbar-nav">
        <li class="nav-item">
          <a class="oldman-menu-link" href="#sidebarUI" data-om-menu-toggle aria-expanded="false">Base UI</a>
          <div class="oldman-submenu hidden" id="sidebarUI" data-om-menu-panel hidden>
            <ul>
              <li><a href="ui-alerts.html" class="oldman-submenu-link">Alerts</a></li>
              <li><a href="ui-modals.html" class="oldman-submenu-link">Modals</a></li>
            </ul>
          </div>
        </li>
      </ul>
      <div id="two-column-menu"></div>
    `;

    const page = new TestBasePage(document.body);

    await page.mount();

    const activeLink = document.querySelector<HTMLAnchorElement>('a[href="ui-modals.html"]')!;
    const parentLink = document.querySelector<HTMLAnchorElement>('a[href="#sidebarUI"]')!;
    const parentCollapse = document.querySelector<HTMLElement>("#sidebarUI")!;
    expect(activeLink.classList.contains("active")).toBe(true);
    expect(parentLink.classList.contains("active")).toBe(true);
    expect(parentLink.getAttribute("aria-expanded")).toBe("true");
    expect(parentCollapse.classList.contains("show")).toBe(true);
  });

  it("does not initialize tooltip and popover shell plugins globally", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <button data-om-tooltip="Tooltip label">Tooltip</button>
      <button data-om-popover="Popover label">Popover</button>
    `;
    const tooltipTrigger = document.querySelector<HTMLElement>("[data-om-tooltip]")!;
    const popoverTrigger = document.querySelector<HTMLElement>("[data-om-popover]")!;
    const page = new TestBasePage(document.body);

    await page.mount();

    expect(tooltipTrigger.dataset.omComponentState).toBeUndefined();
    expect(popoverTrigger.dataset.omComponentState).toBeUndefined();

    await disposePage(page);
  });

  it("mounts back-to-top behavior", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `<button id="back-to-top" style="display: none"></button>`;
    const page = new TestBasePage(document.body);

    await page.mount();
    document.documentElement.scrollTop = 140;
    window.dispatchEvent(new Event("scroll"));

    expect(document.querySelector<HTMLElement>("#back-to-top")?.style.display).toBe("block");
  });

  it("mounts declarative Oldman dropdown behavior", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div data-om-component="dropdown">
        <button type="button" data-om-dropdown-toggle aria-expanded="false">Open</button>
        <div data-om-dropdown-menu hidden class="hidden">Menu</div>
      </div>
    `;
    const trigger = document.querySelector<HTMLButtonElement>("[data-om-dropdown-toggle]")!;
    const menu = document.querySelector<HTMLElement>("[data-om-dropdown-menu]")!;
    const page = new TestBasePage(document.body);

    await page.mount();
    trigger.click();

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(menu.hidden).toBe(false);
    expect(menu.classList.contains("hidden")).toBe(false);
    expect(menu.classList.contains("show")).toBe(true);
  });

  it("forwards consumer sidebar and topbar route options to the EPG adapters", async () => {
    history.replaceState(null, "", "/");
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <aside data-om-sidebar>
        <nav id="navbar-nav"><a href="/portal">Portal</a></nav>
        <div id="two-column-menu"></div>
      </aside>
      <header id="page-topbar">
        <div id="notificationDropdown">
          <section data-om-activity-notifications>
            <div data-om-activity-notification-list></div>
          </section>
        </div>
      </header>
    `;
    const page = new TestBasePage({
      root: document.body,
      sidebarOptions: { defaultDashboardPath: "/portal" },
      topbarOptions: { defaultNotificationHref: "/portal" }
    });

    await page.mount();
    await page.responseActions.run(
      {
        error_code: 0,
        message: "",
        data: {},
        actions: [{ action: "dashboard_activity", title: "Portal notice" }]
      },
      document.querySelector<HTMLElement>("#page-topbar")!
    );

    expect(document.querySelector<HTMLAnchorElement>('#navbar-nav a[href="/portal"]')?.classList.contains("active")).toBe(true);
    expect(document.querySelector<HTMLAnchorElement>('[data-om-activity-notification-item] a[href="/portal"]')?.textContent).toContain("Portal notice");
    await disposePage(page);
  });

  it("decorates Oldman dropdown markup before mounting declarative components", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div class="om-dropdown">
        <button type="button" data-om-dropdown-toggle aria-expanded="false">Open</button>
        <div class="om-dropdown-menu">Menu</div>
      </div>
    `;
    const trigger = document.querySelector<HTMLButtonElement>("[data-om-dropdown-toggle]")!;
    const menu = document.querySelector<HTMLElement>(".om-dropdown-menu")!;
    const page = new TestBasePage(document.body);

    await page.mount();

    expect(document.querySelector<HTMLElement>(".om-dropdown")?.dataset.omComponent).toBe("dropdown");
    expect(menu.hidden).toBe(true);

    trigger.click();

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(menu.hidden).toBe(false);
    expect(menu.classList.contains("show")).toBe(true);
  });

  it("does not globally initialize date/time pickers", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `<input data-provider="flatpickr" data-date-format="d M, Y">`;
    const page = new TestBasePage(document.body);

    await page.mount();

    expect(document.querySelector<HTMLInputElement>('[data-provider="flatpickr"]')?.dataset.omComponentState).toBeUndefined();
  });

  it("does not globally initialize low-frequency color picker demos", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `<div class="classic-colorpicker"></div>`;
    const colorPicker = document.querySelector<HTMLElement>(".classic-colorpicker")!;
    const page = new TestBasePage(document.body);

    await page.mount();

    expect(colorPicker.dataset.omComponentState).toBeUndefined();
    expect(document.querySelector(".pcr-app")).toBeNull();
  });

  it("mounts and unmounts declarative Oldman page components", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `<section data-om-component="test-page-component"></section>`;
    const root = document.querySelector<HTMLElement>("[data-om-component]")!;
    const page = new TestBasePage(document.body);
    page.components.register("test-page-component", TestPageComponent);

    await page.mount();

    expect(root.dataset.testPageComponentMounted).toBe("true");
    expect(root.dataset.omComponentState).toBe("mounted");

    await disposePage(page);

    expect(root.dataset.omComponentState).toBe("unmounted");
  });

  it("registers declarative component loaders before mounting modal dynamic content", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div id="dynamic-modal" data-om-component="modal">
        <div data-om-modal-content></div>
      </div>
    `;
    const page = new TestBasePage({
      root: document.body,
      componentLoaders: {
        "test-page-component": async () => TestPageComponent
      }
    });

    await page.mount();
    const modal = document.querySelector<HTMLElement>("#dynamic-modal")!;
    modal.querySelector<HTMLElement>("[data-om-modal-content]")!.innerHTML = `
      <section id="modal-dynamic-component" data-om-component="test-page-component"></section>
    `;

    const pending: Promise<void>[] = [];
    modal.dispatchEvent(
      new CustomEvent("om:component:before-dynamic-content-mount", {
        bubbles: true,
        detail: {
          root: modal,
          waitUntil(promise: Promise<void>) {
            pending.push(promise);
          }
        }
      })
    );
    await Promise.all(pending);
    await page.components.mount(modal);

    expect(document.querySelector<HTMLElement>("#modal-dynamic-component")?.dataset.testPageComponentMounted).toBe("true");

    await disposePage(page);
  });


  // Full Page replacement, async cleanup and promoted Turbo events are exercised
  // by oldman-web/core/page/lifecycle.test.ts with a real PageRegistry.
  it("shows a scoped loader while a main-frame navigation request is pending", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div id="preloader" data-om-component="preloader" hidden aria-hidden="true" data-om-status="idle"></div>
      <button id="topnav-hamburger-icon" data-om-sidebar-toggle></button>
      <div data-om-sidebar>
        <ul id="navbar-nav"><li><a href="/catalog-channels">Catalog Channels</a></li></ul>
        <div id="two-column-menu"></div>
      </div>
      <header id="page-topbar"></header>
      <turbo-frame id="oldman-main" data-turbo-action="advance">
        <div class="main-content">
          <div id="first-component" data-om-component="frame-probe"></div>
        </div>
      </turbo-frame>
    `;
    history.replaceState(null, "", "/catalog-channels");

    const page = new TestBasePage(document.body);
    page.components.register("frame-probe", TestPageComponent);
    await page.mount();

    const frame = document.querySelector<HTMLElement>("#oldman-main")!;
    const fullscreenPreloader = document.querySelector<HTMLElement>("#preloader")!;
    frame.dispatchEvent(new Event("turbo:before-fetch-request", { bubbles: true }));

    expect(frame.dataset.omFrameState).toBe("loading");
    expect(frame.dataset.omPreloaderStatus).toBe("loading");
    expect(frame.querySelector("[data-om-scoped-preloader]")).not.toBeNull();
    expect(fullscreenPreloader.hidden).toBe(true);

    document.dispatchEvent(new Event("turbo:render"));
    expect(frame.dataset.omFrameState).toBe("loading");
    expect(frame.dataset.omPreloaderStatus).toBe("loading");
    expect(frame.querySelector("[data-om-scoped-preloader]")).not.toBeNull();

    await disposePage(page);
  });


  it("shows declarative initial loading before component mount and hides it on render complete", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div data-om-sidebar>
        <ul id="navbar-nav"><li><a href="/dashboard/analytics">Analytics</a></li></ul>
        <div id="two-column-menu"></div>
      </div>
      <turbo-frame id="oldman-main" data-turbo-action="advance">
        <div id="chart-card-body" data-om-loading-initial="true">
          <div id="chart-component" data-om-component="initial-loading-complete"></div>
        </div>
      </turbo-frame>
    `;

    const page = new TestBasePage(document.body);
    page.components.register("initial-loading-complete", InitialLoadingCompleteComponent);
    await page.mount();

    const scope = document.querySelector<HTMLElement>("#chart-card-body")!;
    const component = document.querySelector<HTMLElement>("#chart-component")!;
    expect(component.dataset.sawInitialLoading).toBe("loading");
    expect(scope.dataset.omPreloaderStatus).toBe("idle");
    expect(scope.querySelector("[data-om-scoped-preloader]")).toBeNull();

    await disposePage(page);
  });




  it("snapshots oldman-main as idle and does not show its loader for Turbo restoration fetches", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div id="preloader" data-om-component="preloader" hidden aria-hidden="true" data-om-status="idle"></div>
      <div data-om-sidebar>
        <ul id="navbar-nav"><li><a href="/catalog-channels">Catalog Channels</a></li></ul>
        <div id="two-column-menu"></div>
      </div>
      <turbo-frame id="oldman-main" data-turbo-action="advance">
        <div class="main-content">Catalog Channels</div>
      </turbo-frame>
    `;
    const page = new TestBasePage(document.body);
    await page.mount();

    const frame = document.querySelector<HTMLElement>("#oldman-main")!;
    expect(frame.dataset.omFrameState).toBe("mounted");
    expect(frame.dataset.omPreloaderStatus).toBe("idle");
    frame.dispatchEvent(
      new CustomEvent("turbo:before-fetch-request", {
        bubbles: true,
        detail: { visit: { action: "restore" } }
      })
    );

    expect(frame.dataset.omFrameState).toBe("mounted");
    expect(frame.dataset.omPreloaderStatus).toBe("idle");
    expect(frame.querySelector("[data-om-scoped-preloader]")).toBeNull();

    await disposePage(page);
  });


  it("turns oldman-main frame-missing responses into full page visits", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div id="preloader" data-om-component="preloader" hidden aria-hidden="true" data-om-status="idle"></div>
      <div data-om-sidebar>
        <ul id="navbar-nav"><li><a href="/dashboard">Dashboard</a></li></ul>
        <div id="two-column-menu"></div>
      </div>
      <turbo-frame id="oldman-main" data-turbo-action="advance">
        <div class="main-content">Dashboard</div>
      </turbo-frame>
    `;

    const page = new TestBasePage(document.body);
    await page.mount();

    const frame = document.querySelector<HTMLElement>("#oldman-main")!;
    frame.dispatchEvent(new Event("turbo:before-fetch-request", { bubbles: true }));

    const response = new Response("<!doctype html><html><body>Sign In</body></html>", {
      status: 200,
      headers: { "content-type": "text/html" }
    });
    const visit = vi.fn();
    const event = new CustomEvent("turbo:frame-missing", {
      bubbles: true,
      cancelable: true,
      detail: { response, visit }
    });

    frame.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(visit).toHaveBeenCalledWith(response);
    expect(frame.dataset.omFrameState).toBe("failed");
    expect(frame.dataset.omPreloaderStatus).toBe("idle");
    expect(frame.querySelector("[data-om-scoped-preloader]")).toBeNull();
    expect(frame.innerHTML).toContain("Dashboard");

    await disposePage(page);
  });

  it("ignores frame-missing events from non-main frames", async () => {
    document.body.dataset.omPage = "test-base";
    document.body.innerHTML = `
      <div data-om-sidebar>
        <ul id="navbar-nav"><li><a href="/dashboard">Dashboard</a></li></ul>
        <div id="two-column-menu"></div>
      </div>
      <turbo-frame id="oldman-main" data-turbo-action="advance">
        <div class="main-content">Dashboard</div>
      </turbo-frame>
      <turbo-frame id="secondary-frame"></turbo-frame>
    `;

    const page = new TestBasePage(document.body);
    await page.mount();

    const response = new Response("<!doctype html><html><body>Other</body></html>");
    const visit = vi.fn();
    const event = new CustomEvent("turbo:frame-missing", {
      bubbles: true,
      cancelable: true,
      detail: { response, visit }
    });

    document.querySelector<HTMLElement>("#secondary-frame")!.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(visit).not.toHaveBeenCalled();

    await disposePage(page);
  });
});
