import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardOverview } from "./dashboard-overview";

describe("DashboardOverview", () => {
  afterEach(() => {
    document.body.replaceChildren();
    history.replaceState(null, "", "/dashboard");
  });

  it("refreshes registered charts when the range changes", async () => {
    document.body.innerHTML = `
      <section data-om-component="dashboard-overview">
        <button data-om-dashboard-range="7d"></button>
      </section>
    `;
    const chart = chartStub("/dashboard/charts/programme-trend");
    const component = new DashboardOverview(root(), { manager: managerStub({ "#programme-trend-chart": chart }) });

    await component.start();
    root().querySelector<HTMLElement>("[data-om-dashboard-range]")?.click();

    await waitFor(() => expect(chart.load).toHaveBeenCalledWith("/dashboard/charts/programme-trend?range=7d"));
    expect(root().querySelector("[data-om-dashboard-range]")?.getAttribute("aria-pressed")).toBe("true");
  });

  it("shows a toast after manual refresh succeeds", async () => {
    document.body.innerHTML = `
      <section data-om-component="dashboard-overview">
        <button data-om-dashboard-refresh></button>
      </section>
    `;
    const chart = chartStub("/dashboard/charts/feed-status");
    const feedback = { toast: vi.fn().mockResolvedValue(undefined) };
    const component = new DashboardOverview(root(), {
      manager: managerStub({ "#feed-status-chart": chart, "#dashboard-feedback": feedback })
    });

    await component.start();
    root().querySelector<HTMLElement>("[data-om-dashboard-refresh]")?.click();

    await waitFor(() => expect(feedback.toast).toHaveBeenCalledWith(expect.objectContaining({ icon: "success", title: "Dashboard refreshed" })));
  });

  it("shows feedback when a chart refresh fails", async () => {
    document.body.innerHTML = `
      <section data-om-component="dashboard-overview">
        <button data-om-dashboard-refresh></button>
      </section>
    `;
    const chart = chartStub("/dashboard/charts/programme-trend", new Error("Bad range"));
    const feedback = { alert: vi.fn().mockResolvedValue({}) };
    const component = new DashboardOverview(root(), {
      manager: managerStub({ "#programme-trend-chart": chart, "#dashboard-feedback": feedback })
    });

    await component.start();
    root().querySelector<HTMLElement>("[data-om-dashboard-refresh]")?.click();

    await waitFor(() =>
      expect(feedback.alert).toHaveBeenCalledWith(
        expect.objectContaining({
          icon: "error",
          text: "Bad range",
          title: "Chart request failed"
        })
      )
    );
  });
});

function root(): HTMLElement {
  return document.querySelector<HTMLElement>("[data-om-component='dashboard-overview']")!;
}

function chartStub(source: string, error?: Error) {
  const chartRoot = document.createElement("section");
  chartRoot.setAttribute("data-om-chart-src", source);
  return {
    load: vi.fn(error ? () => Promise.reject(error) : () => Promise.resolve({})),
    root: chartRoot
  };
}

function managerStub(items: Record<string, unknown>) {
  return {
    get: vi.fn((selector: string) => items[selector] ?? null)
  } as never;
}

async function waitFor(assertion: () => void): Promise<void> {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      if (attempt === 9) throw error;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }
}
