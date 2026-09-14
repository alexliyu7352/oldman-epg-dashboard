import { afterEach, describe, expect, it, vi } from "vitest";
import { IconCatalog } from "./icon-catalog";

describe("IconCatalog", () => {
  afterEach(() => {
    document.body.replaceChildren();
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("filters icons and copies the exact class name", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    document.body.innerHTML = `
      <section>
        <input data-example-icon-search>
        <p data-example-icon-status></p>
        <button data-example-icon-item data-icon-class="ri-search-line"></button>
        <button data-example-icon-item data-icon-class="mdi-alert-outline"></button>
        <div data-example-icon-empty hidden></div>
      </section>
    `;
    const root = document.querySelector<HTMLElement>("section")!;
    const component = new IconCatalog(root);
    await component.start();

    const search = root.querySelector<HTMLInputElement>("input")!;
    search.value = "mdi";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    const items = root.querySelectorAll<HTMLElement>("[data-example-icon-item]");
    const remix = items.item(0);
    const material = items.item(1);
    expect(remix.hidden).toBe(true);
    expect(material.hidden).toBe(false);

    material.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("mdi-alert-outline"));
    expect(root.querySelector("[data-example-icon-status]")?.textContent).toContain("mdi-alert-outline");

    await component.stop();
  });
});
