import { afterEach, describe, expect, it } from "vitest";
import { OldmanDropdown } from "./dropdown";

describe("OldmanDropdown", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("opens declarative Oldman dropdown menus on click", async () => {
    document.body.innerHTML = `
      <div class="om-dropdown" data-om-component="dropdown">
        <button type="button" id="topbarMenu" data-om-dropdown-toggle aria-expanded="false">Open</button>
        <div class="om-dropdown-menu hidden" data-om-dropdown-menu hidden aria-labelledby="topbarMenu">
          <button type="button" class="om-dropdown-item">Item</button>
        </div>
      </div>
    `;
    const trigger = document.querySelector<HTMLElement>("#topbarMenu")!;
    const menu = document.querySelector<HTMLElement>(".om-dropdown-menu")!;
    const component = new OldmanDropdown(document.body);

    await component.start();
    trigger.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(menu.classList.contains("show")).toBe(true);
    expect(menu.hidden).toBe(false);

    await component.stop();
  });
});
