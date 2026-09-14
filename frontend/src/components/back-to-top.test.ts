import { afterEach, describe, expect, it, vi } from "vitest";
import { BackToTopAdapter } from "./back-to-top";

describe("BackToTopAdapter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.replaceChildren();
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  });

  it("shows after scrolling down and scrolls back to top on click", async () => {
    document.body.innerHTML = `<button id="back-to-top" style="display: none"></button>`;
    const scrollTo = vi.fn();
    vi.stubGlobal("scrollTo", scrollTo);
    const component = new BackToTopAdapter(document.body);

    await component.start();
    document.documentElement.scrollTop = 120;
    window.dispatchEvent(new Event("scroll"));

    const button = document.querySelector<HTMLElement>("#back-to-top")!;
    expect(button.style.display).toBe("block");

    button.click();
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
    expect(document.documentElement.scrollTop).toBe(0);

    await component.stop();
  });
});
