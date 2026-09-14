import { describe, expect, it, vi } from "vitest";
import { NotificationsCenter } from "./notifications-center";

describe("NotificationsCenter", () => {
  it("shows feedback when clearing without selected rows", async () => {
    document.body.innerHTML = `
      <main data-om-component="notifications-center">
        <button type="button" data-notifications-clear-selected>Clear Selected</button>
        <div id="notifications-feedback"></div>
        <table><tbody><tr><td><input type="checkbox" data-om-table-select-row></td></tr></tbody></table>
      </main>
    `;
    const root = document.querySelector<HTMLElement>("[data-om-component='notifications-center']")!;
    const component = new NotificationsCenter(root);
    const alert = vi.spyOn(component.feedback(), "alert").mockResolvedValue({ isConfirmed: true } as never);

    await component.start();
    root.querySelector<HTMLButtonElement>("[data-notifications-clear-selected]")!.click();

    expect(alert).toHaveBeenCalledWith(expect.objectContaining({ title: "No notifications selected" }));

    await component.stop();
  });

  it("removes selected notification rows and shows a success toast", async () => {
    document.body.innerHTML = `
      <main data-om-component="notifications-center">
        <button type="button" data-notifications-clear-selected>Clear Selected</button>
        <p data-notifications-empty hidden></p>
        <div id="notifications-feedback"></div>
        <table>
          <tbody>
            <tr><td><input type="checkbox" data-om-table-select-row checked></td><td>Decision</td></tr>
            <tr><td><input type="checkbox" data-om-table-select-row></td><td>Logo</td></tr>
          </tbody>
        </table>
      </main>
    `;
    const root = document.querySelector<HTMLElement>("[data-om-component='notifications-center']")!;
    const component = new NotificationsCenter(root);
    const toast = vi.spyOn(component.feedback(), "toast").mockResolvedValue(undefined);

    await component.start();
    root.querySelector<HTMLButtonElement>("[data-notifications-clear-selected]")!.click();

    expect(root.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(root.textContent).toContain("Logo");
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Notifications cleared" }));
    expect(root.querySelector<HTMLElement>("[data-notifications-empty]")!.hidden).toBe(true);

    await component.stop();
  });
});
