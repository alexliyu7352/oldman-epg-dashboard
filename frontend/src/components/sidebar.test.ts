import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Sidebar } from "./sidebar";

describe("Sidebar", () => {
  beforeEach(() => {
    history.replaceState(null, "", "/dashboard/ui-modals.html");
    document.documentElement.setAttribute("data-layout", "vertical");
    document.documentElement.setAttribute("data-sidebar-size", "lg");
    document.body.innerHTML = `
      <button id="topnav-hamburger-icon"><span class="hamburger-icon"></span></button>
      <div class="vertical-overlay"></div>
      <div id="two-column-menu">legacy</div>
      <div id="scrollbar">
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
      </div>
    `;
  });

  afterEach(() => {
    document.body.replaceChildren();
    document.body.className = "";
    document.documentElement.removeAttribute("data-layout");
    document.documentElement.removeAttribute("data-sidebar-size");
  });

  it("prepares the vertical sidebar and marks the current nested menu active", async () => {
    const sidebar = new Sidebar(document.body);

    await sidebar.start();

    expect(document.querySelector("#two-column-menu")?.childElementCount).toBe(0);
    expect(document.querySelector("#scrollbar")?.hasAttribute("data-simplebar")).toBe(false);
    expect(document.querySelector("#navbar-nav")?.hasAttribute("data-simplebar")).toBe(false);
    expect(document.querySelector<HTMLAnchorElement>('a[href="ui-modals.html"]')?.classList.contains("active")).toBe(
      true
    );
    expect(document.querySelector<HTMLAnchorElement>('a[href="#sidebarUI"]')?.getAttribute("aria-expanded")).toBe(
      "true"
    );
    expect(document.querySelector("#sidebarUI")?.classList.contains("show")).toBe(true);

    await sidebar.stop();
  });

  it("marks backend route links active without relying on html file names", async () => {
    history.replaceState(null, "", "/channels-epg");
    document.body.innerHTML = `
      <ul id="navbar-nav">
        <li class="nav-item">
          <a class="oldman-menu-link" href="#sidebarEpg" data-om-menu-toggle aria-expanded="false">EPG</a>
          <div class="oldman-submenu hidden" id="sidebarEpg" data-om-menu-panel hidden>
            <ul>
              <li><a href="/channels-epg" class="oldman-submenu-link">Channels</a></li>
              <li><a href="/epg-list" class="oldman-submenu-link">Programmes</a></li>
            </ul>
          </div>
        </li>
      </ul>
    `;
    const sidebar = new Sidebar(document.body);

    await sidebar.start();

    expect(document.querySelector<HTMLAnchorElement>('a[href="/channels-epg"]')?.classList.contains("active")).toBe(
      true
    );
    expect(document.querySelector<HTMLAnchorElement>('a[href="#sidebarEpg"]')?.getAttribute("aria-expanded")).toBe(
      "true"
    );
    expect(document.querySelector("#sidebarEpg")?.classList.contains("show")).toBe(true);

    await sidebar.stop();
  });

  it("treats the root dashboard route as the dashboard sidebar link", async () => {
    history.replaceState(null, "", "/");
    document.body.innerHTML = `
      <ul id="navbar-nav">
        <li class="nav-item">
          <button class="oldman-menu-link" data-om-menu-toggle aria-expanded="true">Dashboard</button>
          <div class="oldman-submenu show" id="sidebarDashboard" data-om-menu-panel>
            <a href="/dashboard" class="oldman-submenu-link">Overview</a>
          </div>
        </li>
      </ul>
    `;
    const sidebar = new Sidebar(document.body);

    await sidebar.start();

    expect(document.querySelector<HTMLAnchorElement>('a[href="/dashboard"]')?.classList.contains("active")).toBe(true);
    expect(document.querySelector("#sidebarDashboard")?.classList.contains("show")).toBe(true);
    expect(document.querySelector<HTMLElement>("#sidebarDashboard")?.hidden).toBe(false);

    await sidebar.stop();
  });

  it("marks backend section links active on create and edit sub routes", async () => {
    history.replaceState(null, "", "/epg-list/new");
    document.body.innerHTML = `
      <ul id="navbar-nav">
        <li class="nav-item">
          <a class="oldman-menu-link" href="#sidebarEpg" data-om-menu-toggle aria-expanded="false">EPG</a>
          <div class="oldman-submenu hidden" id="sidebarEpg" data-om-menu-panel hidden>
            <ul>
              <li><a href="/channels-epg" class="oldman-submenu-link">Channels</a></li>
              <li><a href="/epg-list" class="oldman-submenu-link">Programmes</a></li>
            </ul>
          </div>
        </li>
      </ul>
    `;
    const sidebar = new Sidebar(document.body);

    await sidebar.start();

    expect(document.querySelector<HTMLAnchorElement>('a[href="/epg-list"]')?.classList.contains("active")).toBe(true);
    expect(document.querySelector<HTMLAnchorElement>('a[href="#sidebarEpg"]')?.getAttribute("aria-expanded")).toBe(
      "true"
    );

    await sidebar.stop();
  });

  it("prefers an exact backend route over its section fallback", async () => {
    history.replaceState(null, "", "/dashboard/analytics");
    document.body.innerHTML = `
      <ul id="navbar-nav">
        <li class="nav-item">
          <a class="oldman-menu-link" href="#sidebarDashboard" data-om-menu-toggle aria-expanded="false">Dashboard</a>
          <div class="oldman-submenu hidden" id="sidebarDashboard" data-om-menu-panel hidden>
            <ul>
              <li><a href="/dashboard" class="oldman-submenu-link">Overview</a></li>
              <li><a href="/dashboard/analytics" class="oldman-submenu-link">Analytics</a></li>
            </ul>
          </div>
        </li>
      </ul>
    `;
    const sidebar = new Sidebar(document.body);

    await sidebar.start();

    expect(document.querySelector<HTMLAnchorElement>('a[href="/dashboard/analytics"]')?.classList.contains("active")).toBe(
      true
    );
    expect(document.querySelector<HTMLAnchorElement>('a[href="/dashboard"]')?.classList.contains("active")).toBe(false);
    expect(document.querySelector<HTMLAnchorElement>('a[href="#sidebarDashboard"]')?.getAttribute("aria-expanded")).toBe(
      "true"
    );

    await sidebar.stop();
  });

  it("recomputes backend active links after oldman-main frame navigation", async () => {
    history.replaceState(null, "", "/dashboard");
    document.body.innerHTML = `
      <ul id="navbar-nav">
        <li class="nav-item">
          <a class="oldman-menu-link" href="#sidebarDashboard" data-om-menu-toggle aria-expanded="false">Dashboard</a>
          <div class="oldman-submenu hidden" id="sidebarDashboard" data-om-menu-panel hidden>
            <ul>
              <li><a href="/dashboard" class="oldman-submenu-link">Overview</a></li>
            </ul>
          </div>
        </li>
        <li class="nav-item">
          <a class="oldman-menu-link" href="#sidebarEpg" data-om-menu-toggle aria-expanded="false">EPG</a>
          <div class="oldman-submenu hidden" id="sidebarEpg" data-om-menu-panel hidden>
            <ul>
              <li><a href="/channels-epg" class="oldman-submenu-link">Channels</a></li>
            </ul>
          </div>
        </li>
      </ul>
      <turbo-frame id="oldman-main"></turbo-frame>
    `;
    const sidebar = new Sidebar(document.body);

    await sidebar.start();
    expect(document.querySelector<HTMLAnchorElement>('a[href="/dashboard"]')?.classList.contains("active")).toBe(true);

    history.pushState({}, "", "/channels-epg");
    document.querySelector("#oldman-main")?.dispatchEvent(new CustomEvent("turbo:frame-render", { bubbles: true }));

    expect(document.querySelector<HTMLAnchorElement>('a[href="/dashboard"]')?.classList.contains("active")).toBe(false);
    expect(document.querySelector<HTMLAnchorElement>('a[href="/channels-epg"]')?.classList.contains("active")).toBe(
      true
    );
    expect(document.querySelector<HTMLAnchorElement>('a[href="#sidebarEpg"]')?.getAttribute("aria-expanded")).toBe(
      "true"
    );

    await sidebar.stop();
  });

  it("leaves drawer toggling to the declarative sidebar-menu component", async () => {
    const sidebar = new Sidebar(document.body);

    await sidebar.start();
    document.querySelector<HTMLElement>("#topnav-hamburger-icon")?.click();

    expect(document.body.classList.contains("vertical-sidebar-enable")).toBe(false);
    expect(document.documentElement.getAttribute("data-sidebar-size")).toBe("lg");
    expect(document.querySelector(".hamburger-icon")?.classList.contains("open")).toBe(false);

    await sidebar.stop();
  });

  it("clears previously expanded panels before recalculating active state", async () => {
    history.replaceState(null, "", "/channels-epg");
    document.body.innerHTML = `
      <ul id="navbar-nav" class="oldman-menu-list">
        <li class="nav-item">
          <a class="oldman-menu-link active" data-om-menu-toggle aria-expanded="true" href="#first-menu"></a>
          <div class="oldman-submenu show" id="first-menu" data-om-menu-panel>
            <a href="/dashboard" class="oldman-submenu-link active">Overview</a>
          </div>
        </li>
        <li class="nav-item">
          <a class="oldman-menu-link" data-om-menu-toggle aria-expanded="false" href="#second-menu"></a>
          <div class="oldman-submenu hidden" id="second-menu" data-om-menu-panel hidden>
            <a href="/channels-epg" class="oldman-submenu-link">Channels</a>
          </div>
        </li>
      </ul>
    `;
    const sidebar = new Sidebar(document.body);

    await sidebar.start();
    const menuItems = document.querySelectorAll<HTMLElement>("#navbar-nav > li.nav-item");

    expect(document.querySelector("#first-menu")?.classList.contains("show")).toBe(false);
    expect(document.querySelector<HTMLAnchorElement>('#navbar-nav li:first-child > a')?.getAttribute("aria-expanded")).toBe(
      "false"
    );
    expect(document.querySelector<HTMLAnchorElement>('#navbar-nav li:first-child > a')?.classList.contains("active")).toBe(
      false
    );
    expect(menuItems.item(0).classList.contains("active")).toBe(false);
    expect(menuItems.item(0).classList.contains("open")).toBe(false);
    expect(menuItems.item(1).classList.contains("active")).toBe(true);
    expect(menuItems.item(1).classList.contains("open")).toBe(true);
    expect(document.querySelector("#second-menu")?.classList.contains("show")).toBe(true);

    await sidebar.stop();
  });

  it("toggles small-hover sidebar size from the vertical hover control", async () => {
    document.body.innerHTML = `<button id="vertical-hover"></button>`;
    document.documentElement.setAttribute("data-sidebar-size", "lg");
    const sidebar = new Sidebar(document.body);

    await sidebar.start();
    document.querySelector<HTMLElement>("#vertical-hover")?.click();
    expect(document.documentElement.getAttribute("data-sidebar-size")).toBe("sm-hover");

    document.querySelector<HTMLElement>("#vertical-hover")?.click();
    expect(document.documentElement.getAttribute("data-sidebar-size")).toBe("sm-hover-active");

    document.querySelector<HTMLElement>("#vertical-hover")?.click();
    expect(document.documentElement.getAttribute("data-sidebar-size")).toBe("sm-hover");

    await sidebar.stop();
  });

  it("scrolls the active sidebar link into the native sidebar scroll container", async () => {
    history.replaceState(null, "", "/dashboard/ui-modals.html");
    document.body.innerHTML = `
      <div class="app-menu">
        <div class="oldman-sidebar-scroll" id="scrollbar"></div>
        <ul id="navbar-nav">
          <li class="nav-item"><a href="ui-modals.html" class="nav-link">Modals</a></li>
        </ul>
      </div>
    `;
    const activeLink = document.querySelector<HTMLElement>('a[href="ui-modals.html"]')!;
    Object.defineProperty(activeLink, "offsetTop", { configurable: true, value: 420 });
    const sidebar = new Sidebar(document.body);

    await sidebar.start();

    expect(document.querySelector<HTMLElement>(".oldman-sidebar-scroll")?.scrollTop).toBe(420);

    await sidebar.stop();
  });
});
