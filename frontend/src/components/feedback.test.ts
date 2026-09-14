import Swal from "sweetalert2";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OldmanFeedback } from "./feedback";

vi.mock("sweetalert2", () => ({
  default: {
    close: vi.fn(),
    fire: vi.fn(),
    isVisible: vi.fn()
  }
}));

const swalMock = vi.mocked(Swal);

describe("OldmanFeedback", () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.clearAllMocks();
  });

  it("injects Oldman button classes into SweetAlert2", async () => {
    swalMock.fire.mockResolvedValue({ isConfirmed: true } as never);
    const feedback = new OldmanFeedback(document.createElement("div"));

    await feedback.alert({ title: "提示" });

    expect(swalMock.fire).toHaveBeenCalledWith(
      expect.objectContaining({
        buttonsStyling: false,
        customClass: expect.objectContaining({
          cancelButton: "om-button om-button-danger mt-2",
          confirmButton: "om-button om-button-primary mt-2",
          denyButton: "om-button om-button-soft-info mt-2"
        }),
        showCloseButton: true,
        title: "提示"
      })
    );
  });
});
