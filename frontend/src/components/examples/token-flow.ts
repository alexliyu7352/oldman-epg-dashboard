import { Component } from "oldman-web/core";
import { responseReason, showProbeOutcome, type ProbeOutcome } from "./probe-outcome";

type Step = "obtain" | "probe" | "refresh" | "revoke";

interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  refresh_expires_in: number;
}

interface Envelope<TData> {
  data?: TData;
  message?: string;
}

/**
 * 取令牌 → 只凭令牌调用探针 → 刷新 → 注销，四步各有结果区。
 *
 * 每个请求都用 `credentials: "omit"`：同源请求默认会带上登录 cookie，那样探针认出的其实是 session，
 * 演示就不成立了。网络面板里这些请求只有 Authorization 头，没有 Cookie。
 */
export class TokenFlow extends Component {
  static readonly componentName = "token-flow";

  private urls: Record<"obtain" | "refresh" | "revoke" | "probe", string> = { obtain: "", refresh: "", revoke: "", probe: "" };
  private tokens: TokenPair | null = null;

  override async mount(): Promise<void> {
    const { obtainUrl, refreshUrl, revokeUrl, probeUrl } = this.root.dataset;
    if (!obtainUrl || !refreshUrl || !revokeUrl || !probeUrl) throw new Error("Token flow endpoints are missing");
    this.urls = { obtain: obtainUrl, refresh: refreshUrl, revoke: revokeUrl, probe: probeUrl };

    this.on("submit", "[data-token-form]", (event, form) => {
      event.preventDefault();
      void this.run("obtain", () => this.obtain(form as HTMLFormElement));
    });
    this.on("click", "[data-token-step]", (event, trigger) => {
      event.preventDefault();
      const step = (trigger as HTMLElement).dataset.tokenStep as Step | undefined;
      if (step === "probe") void this.run(step, () => this.probe());
      if (step === "refresh") void this.run(step, () => this.refresh());
      if (step === "revoke") void this.run(step, () => this.revoke());
    });
    this.syncSteps();
  }

  private async run(step: Step, action: () => Promise<ProbeOutcome>): Promise<void> {
    const target = this.root.querySelector<HTMLElement>(`[data-token-result="${step}"]`);
    if (!target) return;
    this.setBusy(true);
    target.textContent = this.i18n.t("Sending…");
    try {
      showProbeOutcome(target, await action(), this.i18n.t("Failed"));
    } catch (error) {
      if (this.signal.aborted) return;
      showProbeOutcome(target, { status: 0, lines: [error instanceof Error ? error.message : String(error)], tone: "danger" }, this.i18n.t("Failed"));
    } finally {
      this.setBusy(false);
    }
  }

  private async obtain(form: HTMLFormElement): Promise<ProbeOutcome> {
    const fields = new FormData(form);
    const response = await this.post(this.urls.obtain, {
      username: String(fields.get("username") ?? ""),
      password: String(fields.get("password") ?? "")
    });
    const passwordInput = form.querySelector<HTMLInputElement>("[name='password']");
    if (passwordInput) passwordInput.value = "";
    return this.acceptPair(response, this.i18n.t("Signed in. The page now holds an access token and a refresh token."));
  }

  private async probe(): Promise<ProbeOutcome> {
    if (!this.tokens) return this.noTokens();
    const response = await this.callProbe(this.tokens.access_token);
    if (!response.ok) return { status: response.status, lines: [await this.reason(response)], tone: "danger" };
    const body = (await response.json()) as Envelope<{ username?: string; method?: string }>;
    return {
      status: response.status,
      lines: [
        this.i18n.t("Recognized {username} by {method}.", { username: body.data?.username ?? "?", method: body.data?.method ?? "?" }),
        this.i18n.t("No cookie was sent: the access token alone signed this request in.")
      ],
      tone: "success"
    };
  }

  private async refresh(): Promise<ProbeOutcome> {
    if (!this.tokens) return this.noTokens();
    const response = await this.post(this.urls.refresh, { refresh_token: this.tokens.refresh_token });
    return this.acceptPair(response, this.i18n.t("A new pair was issued. The refresh token it replaced no longer works."));
  }

  private async revoke(): Promise<ProbeOutcome> {
    if (!this.tokens) return this.noTokens();
    const { access_token: accessToken, refresh_token: refreshToken } = this.tokens;
    const revoked = await this.post(this.urls.revoke, { refresh_token: refreshToken }, accessToken);
    if (!revoked.ok) return { status: revoked.status, lines: [await this.reason(revoked)], tone: "danger" };
    this.tokens = null;
    this.showTokens();
    this.syncSteps();

    // 注销时请求头里的访问令牌也一起作废，所以同一个令牌再调探针应当被拒。
    const afterwards = await this.callProbe(accessToken);
    return {
      status: afterwards.status,
      lines: [
        this.i18n.t("Signed out. The probe, called again with the same access token, answered HTTP {status}.", { status: afterwards.status })
      ],
      tone: afterwards.status === 401 ? "success" : "warning"
    };
  }

  private async acceptPair(response: Response, success: string): Promise<ProbeOutcome> {
    if (!response.ok) return { status: response.status, lines: [await this.reason(response)], tone: "danger" };
    const body = (await response.json()) as Envelope<TokenPair>;
    if (!body.data?.access_token || !body.data.refresh_token) throw new Error(this.i18n.t("The token response carried no tokens."));
    this.tokens = body.data;
    this.showTokens();
    this.syncSteps();
    return {
      status: response.status,
      lines: [
        success,
        this.i18n.t("The access token expires in {access} seconds; the refresh token in {refresh} seconds.", {
          access: body.data.expires_in,
          refresh: body.data.refresh_expires_in
        })
      ],
      tone: "success"
    };
  }

  private post(url: string, body: Record<string, string>, accessToken?: string): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return fetch(url, { method: "POST", credentials: "omit", headers, body: JSON.stringify(body), signal: this.signal });
  }

  private callProbe(accessToken: string): Promise<Response> {
    return fetch(this.urls.probe, {
      credentials: "omit",
      headers: { Accept: "application/json", Authorization: `Bearer ${accessToken}` },
      signal: this.signal
    });
  }

  private noTokens(): ProbeOutcome {
    return { status: 0, lines: [this.i18n.t("Get a token first.")], tone: "warning" };
  }

  private reason(response: Response): Promise<string> {
    return responseReason(response, this.i18n.t("Refused"));
  }

  /**
   * 只显示令牌末尾几位：完整值没有展示价值，截断也提醒它是凭据。取末尾而不是开头，因为 JWT 开头是
   * 固定的头部（`eyJhbGciOi…`），每个令牌看起来都一样，刷新前后看不出变化。
   */
  private showTokens(): void {
    for (const [name, value] of [
      ["access", this.tokens?.access_token],
      ["refresh", this.tokens?.refresh_token]
    ] as const) {
      const target = this.root.querySelector<HTMLElement>(`[data-token-value="${name}"]`);
      if (target) target.textContent = value ? `…${value.slice(-12)}` : this.i18n.t("None");
    }
  }

  private syncSteps(): void {
    for (const button of this.root.querySelectorAll<HTMLButtonElement>("[data-token-step]")) {
      button.disabled = this.tokens === null;
    }
  }

  private setBusy(busy: boolean): void {
    for (const button of this.root.querySelectorAll<HTMLButtonElement>("button")) {
      button.disabled = busy || (button.dataset.tokenStep !== undefined && this.tokens === null);
    }
  }
}
