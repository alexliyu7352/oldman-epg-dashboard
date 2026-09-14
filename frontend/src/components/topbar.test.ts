import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Topbar } from "./topbar";

describe("Topbar", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <header id="page-topbar"></header>
      <button data-toggle="fullscreen"></button>
      <button class="light-dark-mode"></button>
    `;
    Object.defineProperty(document.documentElement, "requestFullscreen", {
      configurable: true,
      value: vi.fn(async () => undefined)
    });
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => null
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.replaceChildren();
    document.body.className = "";
    document.documentElement.removeAttribute("data-theme");
  });

  it("handles theme, fullscreen, and topbar shadow interactions", async () => {
    const topbar = new Topbar(document.body);

    await topbar.start();

    document.querySelector<HTMLElement>(".light-dark-mode")?.click();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    document.querySelector<HTMLElement>('[data-toggle="fullscreen"]')?.click();
    expect(document.body.classList.contains("fullscreen-enable")).toBe(true);
    expect(document.documentElement.requestFullscreen).toHaveBeenCalledTimes(1);

    Object.defineProperty(window, "scrollY", { configurable: true, value: 80 });
    window.dispatchEvent(new Event("scroll"));
    expect(document.querySelector("#page-topbar")?.classList.contains("topbar-shadow")).toBe(true);

    await topbar.stop();
  });

  it("manages notification selection state and clears it when the dropdown closes", async () => {
    document.body.innerHTML += `
      <div id="notificationDropdown">
        <section data-om-activity-notifications>
          <div data-om-activity-notification-list>
            <div data-om-activity-notification-item class="notification-item">
              <input data-om-activity-notification-select type="checkbox" />
            </div>
            <div data-om-activity-notification-item class="notification-item">
              <input data-om-activity-notification-select type="checkbox" />
            </div>
            <a data-om-activity-view-all hidden></a>
          </div>
          <div data-om-activity-notification-actions hidden>
            <span data-om-activity-notification-selection-count></span>
          </div>
        </section>
      </div>
    `;
    const topbar = new Topbar(document.body);

    await topbar.start();
    const firstCheck = document.querySelector<HTMLInputElement>("[data-om-activity-notification-select]");
    firstCheck!.checked = true;
    firstCheck!.dispatchEvent(new Event("change", { bubbles: true }));

    expect(firstCheck?.closest(".notification-item")?.classList.contains("active")).toBe(true);
    expect(document.querySelector<HTMLElement>("[data-om-activity-notification-actions]")?.hidden).toBe(false);
    expect(document.querySelector("[data-om-activity-notification-selection-count]")?.textContent).toBe("1");

    document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(firstCheck?.checked).toBe(false);
    expect(firstCheck?.closest(".notification-item")?.classList.contains("active")).toBe(false);
    expect(document.querySelector<HTMLElement>("[data-om-activity-notification-actions]")?.hidden).toBe(true);

    await topbar.stop();
  });

  it("adds transient runtime notifications from the dashboard activity event", async () => {
    document.body.innerHTML += `
      <div id="notificationDropdown">
        <section data-om-activity-notifications>
          <span data-om-activity-notification-count>0</span>
          <div data-om-activity-notification-list>
            <div class="empty-notification-elem"></div>
            <a data-om-activity-view-all hidden></a>
          </div>
        </section>
      </div>
    `;
    const topbar = new Topbar(document.body);

    await topbar.start();
    document.dispatchEvent(
      new CustomEvent("om:notification:add", {
        detail: {
          title: "Password changed",
          description: "<unsafe>",
          href: "/users",
          tone: "success",
          icon: "ri-lock-password-line"
        }
      })
    );

    const activity = document.querySelector("[data-om-activity-notification-item]");
    expect(activity?.textContent).toContain("Password changed");
    expect(activity?.innerHTML).toContain("&lt;unsafe&gt;");
    expect(document.querySelector(".empty-notification-elem")).toBeNull();
    expect(document.querySelector("[data-om-activity-notification-count]")?.textContent).toBe("1");

    await topbar.stop();
  });

  it("keeps the EPG dashboard route as the notification fallback", async () => {
    document.body.innerHTML += `
      <div id="notificationDropdown">
        <section data-om-activity-notifications>
          <div data-om-activity-notification-list></div>
        </section>
      </div>
    `;
    const topbar = new Topbar(document.body);

    await topbar.start();
    document.dispatchEvent(new CustomEvent("om:notification:add", { detail: { title: "No explicit href" } }));

    expect(document.querySelector<HTMLAnchorElement>("[data-om-activity-notification-item] a")?.getAttribute("href")).toBe("/dashboard");
    await topbar.stop();
  });
});
