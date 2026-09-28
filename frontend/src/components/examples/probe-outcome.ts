/** 示例页探针共用：读出被拒请求的原因，把一次调用的结果画成「状态徽标 + 说明」。 */

export interface ProbeOutcome {
  status: number;
  lines: string[];
  tone: "success" | "danger" | "warning";
}

/**
 * 被拒请求的原因。错误响应可能是框架的 JSON（`message`）、Sanic 的错误体（`description`），也可能是
 * 错误页，三种都要能读出点东西；都没有时用 `fallback`。
 */
export async function responseReason(response: Response, fallback: string): Promise<string> {
  const body = await response.text();
  try {
    const parsed = JSON.parse(body) as { message?: string; description?: string };
    return parsed.message || parsed.description || response.statusText || fallback;
  } catch {
    return response.statusText || fallback;
  }
}

/** 用结果替换 `target` 的内容；`status` 为 0 表示请求没发出去，徽标显示 `failedLabel`。 */
export function showProbeOutcome(target: HTMLElement, outcome: ProbeOutcome, failedLabel: string): void {
  target.replaceChildren();
  const badge = document.createElement("span");
  badge.className = `om-badge om-badge-${outcome.tone} mb-2`;
  badge.textContent = outcome.status ? `HTTP ${outcome.status}` : failedLabel;
  target.append(badge);
  outcome.lines.forEach((line, index) => {
    const detail = document.createElement("p");
    detail.className = `${index === outcome.lines.length - 1 ? "mb-0" : "mb-1"} text-default-500`;
    detail.textContent = line;
    target.append(detail);
  });
}
