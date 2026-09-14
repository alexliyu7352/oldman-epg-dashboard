import type { ApexChart } from "oldman-web/components/apex-chart";
import { Component } from "oldman-web/core";
import { EventStreamClient } from "oldman-web/sse";

interface RealtimeChartPayload {
  server_id: number;
  sampled_at: string;
  cpu_percent: number;
  memory_percent: number;
  upload_mbps: number;
  download_mbps: number;
  source: string;
}

/** Drive Chart filters and one page-owned realtime EventSource. */
export class ExampleChart extends Component {
  static readonly componentName = "example-chart";

  private requestSequence = 0;

  override async mount(): Promise<void> {
    this.on("click", "[data-example-chart-source]", (event, trigger) => {
      event.preventDefault();
      if (!(trigger instanceof HTMLElement)) return;
      const source = trigger.dataset.exampleChartSource;
      if (source) void this.load(source);
    });

    const url = this.root.dataset.omRealtimeChartUrl;
    if (!url) return;
    this.listen(this.root, "om:chart:render", () => this.connect(url), { once: true });
  }

  private async load(url: string): Promise<void> {
    const sequence = ++this.requestSequence;
    this.setRequestState(this.i18n.t("Loading chart data"));
    try {
      const result = await this.chart().load(url);
      if (result && sequence === this.requestSequence) this.setRequestState(this.i18n.t("Chart loaded"));
    } catch {
      if (sequence === this.requestSequence) this.setRequestState(this.i18n.t("Chart request failed"));
    }
  }

  private applyRealtime(payload: unknown): void {
    if (!isRealtimeChartPayload(payload)) {
      this.logger.error("Ignored invalid realtime Chart payload", payload);
      return;
    }
    void this.chart().appendData([
      { data: [[payload.sampled_at, payload.cpu_percent]] },
      { data: [[payload.sampled_at, payload.memory_percent]] }
    ]);
    const count = Number(this.root.dataset.exampleRealtimeUpdates ?? "0") + 1;
    this.root.dataset.exampleRealtimeUpdates = String(count);
    const source = this.root.querySelector<HTMLElement>("[data-example-realtime-source]");
    const counter = this.root.querySelector<HTMLElement>("[data-example-realtime-count]");
    if (source) source.textContent = payload.source;
    if (counter) counter.textContent = String(count);
  }

  private connect(url: string): void {
    const client = new EventStreamClient(url);
    client.on<unknown>("examples.chart.metric", (payload) => this.applyRealtime(payload));
    client.onOpen(() => this.setRequestState(this.i18n.t("SSE connected")));
    client.onError(() => this.setRequestState(this.i18n.t("SSE reconnecting")));
    this.cleanup(() => client.close());
  }

  private chart(): ApexChart {
    const selector = this.root.dataset.omChartTarget;
    const chart = selector ? this.manager?.get<ApexChart>(selector) : null;
    if (!chart) throw new Error("Example Chart target is not mounted");
    return chart;
  }

  private setRequestState(message: string): void {
    const target = this.root.querySelector<HTMLElement>("[data-example-chart-request-state]");
    if (target) target.textContent = message;
  }
}

function isRealtimeChartPayload(value: unknown): value is RealtimeChartPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const payload = value as Partial<RealtimeChartPayload>;
  return Number.isInteger(payload.server_id)
    && typeof payload.sampled_at === "string"
    && typeof payload.cpu_percent === "number"
    && typeof payload.memory_percent === "number"
    && typeof payload.upload_mbps === "number"
    && typeof payload.download_mbps === "number"
    && typeof payload.source === "string";
}
