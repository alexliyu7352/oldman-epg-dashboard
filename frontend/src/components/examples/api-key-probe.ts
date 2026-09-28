import { Component } from "oldman-web/core";
import { responseReason, showProbeOutcome, type ProbeOutcome } from "./probe-outcome";

type ProbeName = "session" | "key" | "wrong";

/**
 * 同一个只认 API key 的接口，用三种方式调用：只带登录会话、带粘贴进来的 key、带错的 key。
 *
 * 页面从不拿到任何密钥：key 由读者粘贴。带 key 的两次请求不带 cookie，证明接口认的是 key 而不是会话。
 */
export class ApiKeyProbe extends Component {
  static readonly componentName = "api-key-probe";

  private endpoint = "";
  private header = "";

  override async mount(): Promise<void> {
    this.endpoint = this.root.dataset.probeEndpoint ?? "";
    this.header = this.root.dataset.keyHeader ?? "";
    if (!this.endpoint || !this.header) throw new Error("API key probe endpoint or header is missing");

    this.on("click", "[data-caller-probe]", (event, trigger) => {
      event.preventDefault();
      const probe = (trigger as HTMLElement).dataset.callerProbe as ProbeName | undefined;
      if (probe) void this.run(probe, trigger as HTMLButtonElement);
    });
  }

  private async run(probe: ProbeName, trigger: HTMLButtonElement): Promise<void> {
    const target = this.root.querySelector<HTMLElement>(`[data-caller-result="${probe}"]`);
    if (!target) return;
    const key = this.root.querySelector<HTMLInputElement>("[data-api-key-input]")?.value.trim() ?? "";
    if (probe !== "session" && !key) {
      showProbeOutcome(target, { status: 0, lines: [this.i18n.t("Paste a key from web.auth.api_keys first.")], tone: "warning" }, this.i18n.t("Failed"));
      return;
    }

    trigger.disabled = true;
    target.textContent = this.i18n.t("Sending…");
    try {
      showProbeOutcome(target, await this.describe(await this.send(probe, key)), this.i18n.t("Failed"));
    } catch (error) {
      if (this.signal.aborted) return;
      showProbeOutcome(target, { status: 0, lines: [error instanceof Error ? error.message : String(error)], tone: "danger" }, this.i18n.t("Failed"));
    } finally {
      trigger.disabled = false;
    }
  }

  private send(probe: ProbeName, key: string): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (probe === "session") {
      // 同源请求默认带上登录 cookie：接口认出了会话，但它只收 API key，所以是 403 而不是 401。
      return fetch(this.endpoint, { credentials: "same-origin", headers, signal: this.signal });
    }
    headers[this.header] = probe === "wrong" ? `${key}-wrong` : key;
    return fetch(this.endpoint, { credentials: "omit", headers, signal: this.signal });
  }

  private async describe(response: Response): Promise<ProbeOutcome> {
    if (response.ok) {
      const body = (await response.json()) as { data?: { caller?: string; user_id?: number | null } };
      return {
        status: response.status,
        lines: [
          this.i18n.t("Accepted as the caller {caller}. No user is signed in (user id: {user}).", {
            caller: body.data?.caller ?? "?",
            user: String(body.data?.user_id ?? null)
          })
        ],
        tone: "success"
      };
    }
    return { status: response.status, lines: [await responseReason(response, this.i18n.t("Refused"))], tone: "danger" };
  }
}
