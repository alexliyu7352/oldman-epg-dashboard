import { afterEach, describe, expect, it } from "vitest";
import { OldmanPopover } from "./popover";

describe("OldmanPopover", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("normalizes declarative popover labels inside the component root", async () => {
    document.body.innerHTML = `
      <button data-om-popover="More details">Open</button>
      <button>No popover</button>
    `;
    const trigger = document.querySelector<HTMLElement>("[data-om-popover]")!;
    const component = new OldmanPopover(document.body);

    await component.start();
    expect(trigger.getAttribute("title")).toBe("More details");

    await component.stop();
  });
});
