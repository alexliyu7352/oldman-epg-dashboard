import { Component } from "oldman-web/core";

interface WaitResponse {
  message: string;
}

let componentMounts = 0;
let componentUnmounts = 0;

/** Show real frame/component lifecycle and the existing scoped Loading services. */
export class NavigationProbe extends Component {
  static readonly componentName = "navigation-probe";

  override async mount(): Promise<void> {
    componentMounts += 1;
    this.renderLifecycle();
    this.on("click", "[data-example-loading-scope]", async (event, matchedElement) => {
      event.preventDefault();
      await this.runLoading(matchedElement as HTMLElement);
    });
  }

  override async beforeUnmount(): Promise<void> {
    componentUnmounts += 1;
  }

  private renderLifecycle(): void {
    const values: Array<[string, string]> = [
      ["[data-example-page-mounts]", document.body.dataset.omExamplesPageMounts ?? "0"],
      ["[data-example-component-mounts]", String(componentMounts)],
      ["[data-example-component-unmounts]", String(componentUnmounts)],
      ["[data-example-frame-state]", document.querySelector<HTMLElement>("#oldman-main")?.dataset.omFrameState ?? "unknown"]
    ];
    for (const [selector, value] of values) {
      const element = this.root.querySelector<HTMLElement>(selector);
      if (element) element.textContent = value;
    }
  }

  private async runLoading(trigger: HTMLElement): Promise<void> {
    const result = this.root.querySelector<HTMLElement>("[data-example-loading-result]");
    if (!result) throw new Error("Navigation Loading result is missing");
    const preloader = trigger.dataset.exampleLoadingScope === "page" ? this.page?.preloader : this.preloader;
    if (!preloader) throw new Error("Navigation Loading scope is unavailable");

    try {
      const response = await preloader.withLoading(
        () => this.http.getJson<WaitResponse>("/examples/navigation/loading/wait"),
        { message: this.i18n.t("Loading...") }
      );
      result.textContent = response.message;
    } catch (error) {
      if (this.signal.aborted) return;
      this.logger.error("Navigation Loading example failed", error);
      await this.page?.feedback?.alert({ icon: "error", titleText: this.i18n.t("Request failed") });
    }
  }
}
