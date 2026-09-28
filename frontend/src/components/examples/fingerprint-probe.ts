import { Component, FINGERPRINT_HEADER } from "oldman-web/core";
import { fingerprintSender } from "@app/security/fingerprint";
import { responseReason, showProbeOutcome, type ProbeOutcome } from "./probe-outcome";

type ProbeName = "valid" | "missing" | "tampered" | "flood";

/** 同一个受保护端点，用四种方式调用，四个结果并排放着好对照。 */
export class FingerprintProbe extends Component {
  static readonly componentName = "fingerprint-probe";

  private endpoint = "";

  override async mount(): Promise<void> {
    this.endpoint = this.root.dataset.probeEndpoint ?? "";
    if (!this.endpoint) throw new Error("Fingerprint probe endpoint is missing");

    await this.showVisitorId();
    this.on("click", "[data-probe]", (event, matchedElement) => {
      event.preventDefault();
      const probe = (matchedElement as HTMLElement).dataset.probe as ProbeName | undefined;
      if (probe) void this.run(probe, matchedElement as HTMLButtonElement);
    });
  }

  private async showVisitorId(): Promise<void> {
    const target = this.root.querySelector<HTMLElement>("[data-probe-visitor]");
    if (!target) return;
    // 只显示前 8 位：完整值没有展示价值，截断也提醒它不是什么公开标识。
    target.textContent = `${(await fingerprintSender().visitorId()).slice(0, 8)}…`;
  }

  private async run(probe: ProbeName, trigger: HTMLButtonElement): Promise<void> {
    const target = this.root.querySelector<HTMLElement>(`[data-probe-result="${probe}"]`);
    if (!target) return;

    trigger.disabled = true;
    target.textContent = this.i18n.t("Sending…");
    try {
      showProbeOutcome(target, probe === "flood" ? await this.flood(target) : await this.single(probe), this.i18n.t("Failed"));
    } catch (error) {
      showProbeOutcome(
        target,
        { status: 0, lines: [error instanceof Error ? error.message : String(error)], tone: "danger" },
        this.i18n.t("Failed")
      );
    } finally {
      trigger.disabled = false;
    }
  }

  private async headersFor(probe: ProbeName): Promise<Record<string, string>> {
    const base = { Accept: "application/json" };
    if (probe === "missing") return base;

    const payload = await fingerprintSender().payload();
    if (probe !== "tampered") return { ...base, [FINGERPRINT_HEADER]: payload };

    // 改掉最后一个字符：AES-GCM 会认证失败，服务端拒绝而不是解出别的东西。
    const last = payload.at(-1) === "A" ? "B" : "A";
    return { ...base, [FINGERPRINT_HEADER]: `${payload.slice(0, -1)}${last}` };
  }

  private async single(probe: ProbeName): Promise<ProbeOutcome> {
    const response = await fetch(this.endpoint, { headers: await this.headersFor(probe) });
    return this.describe(response);
  }

  private async flood(target: HTMLElement): Promise<ProbeOutcome> {
    const headers = await this.headersFor("valid");
    for (let attempt = 1; attempt <= 40; attempt += 1) {
      const response = await fetch(this.endpoint, { headers });
      if (response.status === 429) {
        const outcome = await this.describe(response);
        return { ...outcome, lines: [this.i18n.t("Refused on request {count}. {detail}", { count: attempt, detail: outcome.lines.join(" ") })] };
      }
      target.textContent = this.i18n.t("Sent {count}, still accepted…", { count: attempt });
    }
    return { status: 200, lines: [this.i18n.t("Still accepted after 40 requests; the limit for this probe is higher than expected.")], tone: "warning" };
  }

  private async describe(response: Response): Promise<ProbeOutcome> {
    if (response.ok) {
      const body = (await response.json()) as { data?: { fingerprint_count?: number; ip_count?: number } };
      const counts = body.data ?? {};
      return {
        status: response.status,
        lines: [
          this.i18n.t("Accepted. Seen {fp} times from this fingerprint and {ip} from this address in the current window.", {
            fp: counts.fingerprint_count ?? 0,
            ip: counts.ip_count ?? 0
          })
        ],
        tone: "success"
      };
    }

    const retryAfter = response.headers.get("Retry-After");
    const reason = await responseReason(response, this.i18n.t("Refused"));
    if (response.status === 429) {
      return {
        status: 429,
        lines: [retryAfter ? this.i18n.t("{reason} Retry after {seconds} seconds.", { reason, seconds: retryAfter }) : reason],
        tone: "warning"
      };
    }
    return { status: response.status, lines: [reason], tone: "danger" };
  }
}
