import { describe, expect, it, vi } from "vitest";
import { OldmanModal } from "./modal";

describe("OldmanModal", () => {
  it("opens and closes Oldman modal markup", async () => {
    vi.useFakeTimers();
    document.body.innerHTML = `
      <button type="button" data-om-modal-target="#user-modal">Open</button>
      <div id="user-modal" class="om-modal" hidden aria-hidden="true" style="display: none;">
        <div class="om-modal-dialog">
          <div class="om-modal-surface">
            <div class="om-modal-header">
              <h5 class="om-modal-title">编辑用户</h5>
              <button type="button" class="om-close-button" data-om-modal-close aria-label="Close"></button>
            </div>
            <div class="om-modal-body">
              <button type="button" id="save-button">保存</button>
            </div>
            <div class="om-modal-footer"></div>
          </div>
        </div>
      </div>
    `;
    const root = document.querySelector<HTMLElement>("#user-modal")!;
    const modal = new OldmanModal(root);

    await modal.start();
    document.querySelector<HTMLButtonElement>("[data-om-modal-target]")!.click();
    await vi.waitFor(() => expect(root.hidden).toBe(false));

    const backdrop = document.querySelector<HTMLElement>("[data-om-modal-backdrop]")!;
    expect(root.classList.contains("is-open")).toBe(true);
    expect(root.style.display).toBe("block");
    expect(root.getAttribute("role")).toBe("dialog");
    expect(root.getAttribute("aria-modal")).toBe("true");
    expect(document.body.classList.contains("om-modal-open")).toBe(true);
    expect(backdrop.className).toContain("om-modal-backdrop");
    expect(Number.parseInt(backdrop.style.minHeight || "0", 10)).toBeGreaterThanOrEqual(window.innerHeight);

    document.querySelector<HTMLButtonElement>("[data-om-modal-close]")!.click();

    expect(root.hidden).toBe(false);
    expect(root.dataset.omState).toBe("closing");
    expect(root.classList.contains("is-open")).toBe(false);
    expect(root.style.display).toBe("block");

    vi.advanceTimersByTime(250);
    await Promise.resolve();

    expect(root.hidden).toBe(true);
    expect(root.classList.contains("is-open")).toBe(false);
    expect(root.style.display).toBe("none");
    expect(document.body.classList.contains("om-modal-open")).toBe(false);
    expect(document.querySelector("[data-om-modal-backdrop]")).toBeNull();

    await modal.stop();
    vi.useRealTimers();
  });

  it("writes remote fragments into Oldman title, body, and footer regions", async () => {
    document.body.innerHTML = `
      <button type="button" data-om-modal-target="#user-modal" data-om-modal-url="/users/anna/edit">Edit</button>
      <div id="user-modal" class="om-modal" hidden aria-hidden="true" style="display: none;">
        <div class="om-modal-dialog">
          <div class="om-modal-surface">
            <div class="om-modal-header"><h5 class="om-modal-title"></h5></div>
            <div class="om-modal-body"></div>
            <div class="om-modal-footer"></div>
          </div>
        </div>
      </div>
    `;
    const root = document.querySelector<HTMLElement>("#user-modal")!;
    const modal = new OldmanModal(root);
    Object.assign(modal.http, {
      getJson: vi.fn().mockResolvedValue({
        title: "编辑 Anna",
        html: "<form><input name='name' value='Anna'></form>",
        footer: "<button type='submit'>保存</button>"
      })
    });

    await modal.start();
    document.querySelector<HTMLButtonElement>("[data-om-modal-url]")!.click();
    await vi.waitFor(() => expect(root.hidden).toBe(false));

    expect(modal.http.getJson).toHaveBeenCalledWith("/users/anna/edit");
    expect(root.querySelector<HTMLElement>(".om-modal-title")?.textContent).toBe("编辑 Anna");
    expect(root.querySelector<HTMLElement>(".om-modal-body")?.innerHTML).toContain("name=\"name\"");
    expect(root.querySelector<HTMLElement>(".om-modal-footer")?.innerHTML).toContain("保存");

    modal.close("programmatic");
    await modal.stop();
  });

  it("normalizes initially closed Oldman modal markup", async () => {
    document.body.innerHTML = `
      <button type="button" data-om-modal-target="#settings-modal">Open</button>
      <div id="settings-modal" class="om-modal" aria-hidden="true" style="display: none;">
        <div class="om-modal-dialog">
          <div class="om-modal-surface">
            <div class="om-modal-body">内容</div>
          </div>
        </div>
      </div>
    `;
    const root = document.querySelector<HTMLElement>("#settings-modal")!;
    const modal = new OldmanModal(root);

    await modal.start();

    expect(root.hidden).toBe(true);
    expect(root.style.display).toBe("none");

    document.querySelector<HTMLButtonElement>("[data-om-modal-target]")!.click();
    await vi.waitFor(() => expect(root.hidden).toBe(false));

    expect(root.classList.contains("is-open")).toBe(true);
    expect(root.style.display).toBe("block");

    await modal.stop();
  });

  it("respects static Oldman backdrop and keyboard configuration", async () => {
    document.body.innerHTML = `
      <button type="button" data-om-modal-target="#static-modal">Open</button>
      <div id="static-modal" class="om-modal" data-om-backdrop="static" data-om-keyboard="false" aria-hidden="true" style="display: none;">
        <div class="om-modal-dialog">
          <div class="om-modal-surface">
            <div class="om-modal-body">内容</div>
          </div>
        </div>
      </div>
    `;
    const root = document.querySelector<HTMLElement>("#static-modal")!;
    const modal = new OldmanModal(root);

    await modal.start();
    document.querySelector<HTMLButtonElement>("[data-om-modal-target]")!.click();
    await vi.waitFor(() => expect(root.hidden).toBe(false));

    root.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

    expect(root.hidden).toBe(false);
    expect(root.dataset.omState).toBe("open");

    modal.close("programmatic");
    await modal.stop();
  });
});
