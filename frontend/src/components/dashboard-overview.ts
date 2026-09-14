import { Component } from "oldman-web/core";
import type { ApexChart } from "oldman-web/components/apex-chart";
import type { OldmanFeedback } from "./feedback";

const CHART_SELECTORS = ["#programme-trend-chart", "#feed-status-chart", "#logo-quality-chart"];

/**
 * Dashboard Overview 页面交互组件，负责外部按钮调用图表公开 API。
 */
export class DashboardOverview extends Component {
  static readonly componentName = "dashboard-overview";

  private currentRange = "30d";

  /**
   * 绑定时间范围、刷新和失败反馈按钮。
   */
  override async mount(): Promise<void> {
    this.root.dataset.omDashboardOverviewMounted = "true";
    this.currentRange = this.root.querySelector<HTMLElement>("[data-om-dashboard-range].active")?.dataset.omDashboardRange || this.currentRange;

    this.on("click", "[data-om-dashboard-range]", (event, trigger) => {
      event.preventDefault();
      if (!(trigger instanceof HTMLElement)) return;
      const range = trigger.dataset.omDashboardRange || this.currentRange;
      this.root.dataset.omDashboardLastAction = `range:${range}`;
      this.activateRange(trigger, range);
      void this.reloadCharts(range).catch((error) => this.showChartFailure(error));
    });

    this.on("click", "[data-om-dashboard-refresh]", (event) => {
      event.preventDefault();
      this.root.dataset.omDashboardLastAction = "refresh";
      void this.reloadCharts(this.currentRange)
        .then(() => this.showRefreshToast())
        .catch((error) => this.showChartFailure(error));
    });
  }

  private activateRange(trigger: HTMLElement, range: string): void {
    this.currentRange = range;
    for (const item of this.$$<HTMLElement>("[data-om-dashboard-range]")) {
      const active = item === trigger;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  private async reloadCharts(range: string): Promise<void> {
    const charts = this.dashboardCharts();
    await Promise.all(charts.map((chart) => chart.load(this.chartUrl(chart, range))));
  }

  private async showChartFailure(error: unknown): Promise<void> {
    await this.feedback()?.alert({
      icon: "error",
      text: error instanceof Error ? error.message : this.i18n.t("Request failed"),
      title: this.i18n.t("Chart request failed")
    });
  }

  private dashboardCharts(): ApexChart[] {
    const charts = CHART_SELECTORS.flatMap((selector) => {
      const chart = this.manager?.get<ApexChart>(selector);
      return chart ? [chart] : [];
    });
    this.root.dataset.omDashboardChartCount = String(charts.length);
    return charts;
  }

  private chartUrl(chart: ApexChart, range: string): string {
    const source = chart.root.getAttribute("data-om-chart-src") || "";
    const url = new URL(source, window.location.href);
    url.searchParams.set("range", range);
    return `${url.pathname}${url.search}`;
  }

  private feedback(): OldmanFeedback | null {
    return this.manager?.get<OldmanFeedback>("#dashboard-feedback") ?? null;
  }

  private showRefreshToast(): void {
    void this.feedback()?.toast({
      icon: "success",
      title: this.i18n.t("Dashboard refreshed")
    });
  }
}
