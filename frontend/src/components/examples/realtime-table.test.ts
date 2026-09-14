import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RealtimeTable } from "./realtime-table";

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];

  closeCalls = 0;

  constructor(readonly url: string | URL) {
    super();
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closeCalls += 1;
  }

  emit(data: unknown): void {
    this.dispatchEvent(new MessageEvent("examples.table.metrics", { data: JSON.stringify(data) }));
  }
}

describe("RealtimeTable", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("CSS", { escape: vi.fn((value: string) => value.replaceAll(":", "\\:")) });
    document.body.innerHTML = `
      <section data-om-component="realtime-table" data-om-realtime-url="/examples/tables/realtime-events">
        <table><tbody>
          <tr data-om-table-row data-om-table-row-id="1">
            <td data-om-column="cpu_percent">10.00%</td>
            <td data-om-column="upload_mbps">20.00 Mbps</td>
          </tr>
        </tbody></table>
      </section>
    `;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it("updates only visible row cells from a valid event", async () => {
    const component = new RealtimeTable(root());
    await component.start();

    source().emit({
      rows: [
        { id: 1, cells: { cpu_percent: "37.25%", upload_mbps: "61.50 Mbps" } },
        { id: 999, cells: { cpu_percent: "99.00%" } }
      ]
    });

    expect(cell("cpu_percent").textContent).toBe("37.25%");
    expect(cell("upload_mbps").textContent).toBe("61.50 Mbps");
    expect(CSS.escape).toHaveBeenCalledWith("1");

    await component.stop();
    expect(source().closeCalls).toBe(1);
  });

  it("ignores an entire invalid batch and closes the stream on unmount", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const component = new RealtimeTable(root());
    await component.start();

    source().emit({
      rows: [
        { id: 1, cells: { cpu_percent: "37.25%" } },
        { id: 2, cells: { cpu_percent: 50 } }
      ]
    });

    expect(cell("cpu_percent").textContent).toBe("10.00%");
    expect(error).toHaveBeenCalledOnce();

    await component.stop();
    source().emit({ rows: [{ id: 1, cells: { cpu_percent: "88.00%" } }] });
    expect(cell("cpu_percent").textContent).toBe("10.00%");
    expect(source().closeCalls).toBe(1);
  });
});

function root(): HTMLElement {
  return document.querySelector<HTMLElement>("[data-om-component='realtime-table']")!;
}

function cell(name: string): HTMLElement {
  return root().querySelector<HTMLElement>(`[data-om-column='${name}']`)!;
}

function source(): FakeEventSource {
  const current = FakeEventSource.instances.at(-1);
  if (!current) throw new Error("RealtimeTable did not create an EventSource");
  return current;
}
