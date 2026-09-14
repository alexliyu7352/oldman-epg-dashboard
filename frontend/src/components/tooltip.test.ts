import { afterEach, describe, expect, it } from "vitest";
import { OldmanTooltip } from "./tooltip";

describe("OldmanTooltip", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("normalizes declarative tooltip labels inside the component root", async () => {
    document.body.innerHTML = `
      <button data-om-tooltip="Inspect details">Inspect</button>
      <button>No tooltip</button>
    `;
    const trigger = document.querySelector<HTMLElement>("[data-om-tooltip]")!;
    const component = new OldmanTooltip(document.body);

    await component.start();
    expect(trigger.getAttribute("title")).toBe("Inspect details");

    await component.stop();
  });
});
