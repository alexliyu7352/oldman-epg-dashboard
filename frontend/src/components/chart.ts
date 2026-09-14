import { ApexChart, type ApexChartInstance, type ApexChartOptions } from "oldman-web/components/apex-chart";
import type { ComponentOptions } from "oldman-web/core";

export interface ApexChartMountResult {
  chart: ApexChartInstance | null;
  component: ApexChart;
  element: HTMLElement;
}

export type ApexChartOptionsFactory = (colors: string[]) => ApexChartOptions;

/**
 * 按 当前模板 的 `data-colors` 约定解析图表颜色，并交给 Oldman ApexChart 托管生命周期。
 */
export async function mountApexChart(
  root: HTMLElement,
  id: string,
  options: ComponentOptions,
  optionsFactory: ApexChartOptionsFactory
): Promise<ApexChartMountResult | null> {
  const element = root.querySelector<HTMLElement>(`#${id}`);
  if (!element) return null;

  element.dataset.omComponent = "apex-chart";
  const component = new ApexChart(element, options);
  await component.start();
  const chart = await component.renderChart(optionsFactory(resolveChartColors(root, id)));
  return { chart, component, element };
}

/**
 * 把 当前模板 `data-colors` 和 `data-colors-{theme}` CSS 变量解析为 ApexCharts 可以直接使用的颜色值。
 */
export function resolveChartColors(root: HTMLElement, id: string): string[] {
  const element = root.querySelector<HTMLElement>(`#${id}`);
  const theme = document.documentElement.getAttribute("data-theme") || "";
  const encoded = element?.getAttribute(`data-colors-${theme}`) || element?.getAttribute("data-colors");
  if (!encoded) return [];

  return (JSON.parse(encoded) as string[]).map((color) => {
    const normalized = color.replace(" ", "");
    if (normalized.includes(",")) {
      const [cssVariable, opacity] = color.split(",");
      if (!cssVariable || !opacity) return normalized;
      return `rgba(${getComputedStyle(document.documentElement).getPropertyValue(cssVariable)},${opacity})`;
    }

    return getComputedStyle(document.documentElement).getPropertyValue(normalized).trim() || normalized;
  });
}

/**
 * 等待 sidebar、网格和字体测量稳定后，再让 ApexCharts 读取容器尺寸。
 */
export async function waitForChartLayout(): Promise<void> {
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
}
