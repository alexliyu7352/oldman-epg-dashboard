import { afterEach, describe, expect, it } from "vitest";
import type { ResponseActionContext } from "oldman-web/core";
import { ExamplesPage } from "./examples";

describe("ExamplesPage", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("handles the demo-private marker action and delegates unknown actions", async () => {
    document.body.innerHTML = '<main><output id="private-action-result"></output></main>';
    const page = new ExamplesPage(document.body);
    const source = document.querySelector<HTMLElement>("main")!;
    const context = {
      page,
      response: { error_code: 0, message: "", data: {}, actions: [] },
      source
    } satisfies ResponseActionContext;

    await expect(page.handleResponseAction({
      action: "example_mark",
      target: "#private-action-result",
      text: "Private action completed"
    }, context)).resolves.toBe(true);
    expect(document.querySelector("#private-action-result")?.textContent).toBe("Private action completed");
    await expect(page.handleResponseAction({ action: "not-owned" }, context)).resolves.toBe(false);
  });
});
