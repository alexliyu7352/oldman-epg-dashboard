import { afterEach, describe, expect, it, vi } from "vitest";
import { DropdownTabs } from "./dropdown-tabs";

describe("DropdownTabs", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("shows dropdown menu tabs without letting the click bubble to the dropdown", async () => {
    document.body.innerHTML = `
      <div class="om-dropdown-menu">
        <div class="nav">
          <a href="#first-tab" data-om-tab-target="#first-tab" aria-selected="false">First</a>
        </div>
      </div>
      <div id="first-tab" data-om-tab-panel hidden class="hidden"></div>
    `;
    const dropdownClick = vi.fn();
    const trigger = document.querySelector<HTMLAnchorElement>("[data-om-tab-target]")!;
    trigger.closest(".om-dropdown-menu")?.addEventListener("click", dropdownClick);
    const panel = document.querySelector<HTMLElement>("#first-tab")!;
    const component = new DropdownTabs(document.body);

    await component.start();
    trigger.click();

    expect(dropdownClick).not.toHaveBeenCalled();
    expect(trigger.classList.contains("active")).toBe(true);
    expect(trigger.getAttribute("aria-selected")).toBe("true");
    expect(panel.hidden).toBe(false);
    expect(panel.classList.contains("hidden")).toBe(false);

    await component.stop();
  });
});
