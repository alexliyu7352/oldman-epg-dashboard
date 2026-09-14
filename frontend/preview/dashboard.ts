import nunjucks from "nunjucks";

type NunjucksKeywordArguments = {
  __keywords?: boolean;
  html_id?: string;
};

/**
 * 提供与后端 ChartView 相同的模板表面，只替换预览时无法连接的 HTTP 数据端点。
 */
class PreviewChart {
  constructor(private readonly endpoint: string) {}

  render_shell(arguments_: NunjucksKeywordArguments = {}): nunjucks.runtime.SafeString {
    const htmlId = arguments_.html_id ?? "preview-chart";
    return new nunjucks.runtime.SafeString(
      `<div id="${htmlId}" data-om-component="apex-chart" data-om-chart-src="${this.endpoint}"></div>`
    );
  }
}

const notifications = [
  {
    description: "Two upstream records require review.",
    href: "/upstream-records",
    icon: "ri-cloud-line",
    time: "5 minutes ago",
    title: "Upstream review",
    tone: "warning"
  }
];

/**
 * 返回 dashboard 真实模板所需的最小完整上下文。
 */
export function dashboardPreviewContext(): Record<string, unknown> {
  return {
    active_page: "dashboard_overview",
    active_section: "dashboard",
    current_year: 2026,
    dashboard_notifications: notifications,
    feed_status_chart: new PreviewChart("/dashboard/charts/feed-status"),
    locale: "zh-CN",
    logo_quality_chart: new PreviewChart("/dashboard/charts/logo-quality"),
    programme_trend_chart: new PreviewChart("/dashboard/charts/programme-trend"),
    request: {
      ctx: {
        session: {
          get(key: string, fallback = "") {
            return key === "display_name" ? "Administrator" : fallback;
          }
        }
      }
    },
    stats: {
      catalog_feed_count: 4,
      channel_count: 128,
      epg_count: 2496,
      logo_asset_count: 116,
      logo_coverage_percent: 90.6,
      pending_decision_count: 3,
      recent_anomaly_records: [
        {
          disappeared_at: "2026-07-12 08:30",
          primary_name: "Oldman News",
          source_code: "demo",
          source_record_key: "channel-101"
        }
      ],
      recent_decisions: [
        {
          decision: "review",
          decision_scope: "channel",
          reason: "Name changed upstream"
        }
      ],
      upstream_record_count: 184
    }
  };
}
