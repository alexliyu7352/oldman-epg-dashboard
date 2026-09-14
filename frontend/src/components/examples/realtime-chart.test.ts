import { afterEach, describe, expect, it, vi } from "vitest";
import { createI18n } from "oldman-web/core";
import { ExampleChart } from "./realtime-chart";

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];
  readonly close = vi.fn();

  constructor(readonly url: string | URL) {
    super();
    FakeEventSource.instances.push(this);
  }
}

describe("ExampleChart", () => {
  afterEach(() => {
    FakeEventSource.instances = [];
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it("reloads the mounted public Chart from declarative controls", async () => {
    document.body.innerHTML = `
      <section data-om-chart-target="#chart">
        <button data-example-chart-source="/charts?range=7d"></button>
        <p data-example-chart-request-state></p>
      </section>
      <div id="chart"></div>
    `;
    const chart = { load: vi.fn().mockResolvedValue({ series: [1] }) };
    const component = new ExampleChart(root(), {
      i18n: createI18n({
        locale: "zh-Hans",
        messages: {
          "Chart loaded": "图表已加载",
          "Loading chart data": "正在加载图表数据"
        }
      }),
      manager: manager(chart)
    });
    await component.start();

    root().querySelector<HTMLElement>("button")!.click();
    await vi.waitFor(() => expect(chart.load).toHaveBeenCalledWith("/charts?range=7d"));
    expect(root().querySelector("[data-example-chart-request-state]")?.textContent).toBe("图表已加载");

    await component.stop();
  });

  it("appends typed SSE values and closes the page-owned connection", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    document.body.innerHTML = `
      <section data-om-chart-target="#chart" data-om-realtime-chart-url="/events">
        <strong data-example-realtime-source></strong>
        <strong data-example-realtime-count>0</strong>
        <p data-example-chart-request-state></p>
      </section>
      <div id="chart"></div>
    `;
    const chart = { appendData: vi.fn().mockResolvedValue(undefined) };
    const component = new ExampleChart(root(), {
      i18n: createI18n({ locale: "zh-Hans", messages: { "SSE connected": "SSE 已连接" } }),
      manager: manager(chart)
    });
    await component.start();
    root().dispatchEvent(new CustomEvent("om:chart:render"));
    source().dispatchEvent(new Event("open"));

    source().dispatchEvent(new MessageEvent("examples.chart.metric", {
      data: JSON.stringify({
        server_id: 1,
        sampled_at: "2026-09-02T10:00:00",
        cpu_percent: 42,
        memory_percent: 61,
        upload_mbps: 100,
        download_mbps: 120,
        source: "stream"
      })
    }));

    expect(chart.appendData).toHaveBeenCalledWith([
      { data: [["2026-09-02T10:00:00", 42]] },
      { data: [["2026-09-02T10:00:00", 61]] }
    ]);
    expect(root().dataset.exampleRealtimeUpdates).toBe("1");
    expect(root().querySelector("[data-example-realtime-source]")?.textContent).toBe("stream");
    expect(root().querySelector("[data-example-chart-request-state]")?.textContent).toBe("SSE 已连接");

    await component.stop();
    expect(source().close).toHaveBeenCalledTimes(1);
  });
});

function root(): HTMLElement {
  return document.querySelector<HTMLElement>("section")!;
}

function source(): FakeEventSource {
  return FakeEventSource.instances[0]!;
}

function manager(chart: unknown) {
  return { get: vi.fn(() => chart) } as never;
}
