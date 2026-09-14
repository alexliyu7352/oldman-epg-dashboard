import { setupPage, type ResponseAction, type ResponseActionContext } from "oldman-web/core";
import { BasePage } from "./base-page";

let pageMountCount = 0;

/** Shared lifecycle entry for every Dashboard example page. */
export class ExamplesPage extends BasePage {
  constructor(root: HTMLElement = document.documentElement) {
    super({
      root,
      componentLoaders: {
        "feedback-workflow": async () => (await import("@app/components/examples/feedback-workflow")).FeedbackWorkflow,
        "navigation-probe": async () => (await import("@app/components/examples/navigation-probe")).NavigationProbe,
        "example-chart": async () => (await import("@app/components/examples/realtime-chart")).ExampleChart,
        "realtime-table": async () => (await import("@app/components/examples/realtime-table")).RealtimeTable,
        "icon-catalog": async () => (await import("@app/components/examples/icon-catalog")).IconCatalog
      }
    });
  }

  override async mount(): Promise<void> {
    pageMountCount += 1;
    document.body.dataset.omExamplesPageMounts = String(pageMountCount);
    await super.mount();
    document.body.dataset.omExamplesReady = "true";
  }

  protected override async afterContentMounted(root: ParentNode): Promise<void> {
    await super.afterContentMounted(root);
    this.i18n.translateDocument(root);
  }

  override async beforeUnmount(): Promise<void> {
    delete document.body.dataset.omExamplesReady;
    delete document.body.dataset.omExamplesPageMounts;
    await super.beforeUnmount();
  }

  override async handleResponseAction(
    action: ResponseAction,
    context: ResponseActionContext
  ): Promise<boolean> {
    if (action.action !== "example_mark") return super.handleResponseAction(action, context);
    if (typeof action.target !== "string" || typeof action.text !== "string") {
      throw new Error("Example mark Action requires a target and text");
    }
    const target = this.root.querySelector<HTMLElement>(action.target);
    if (!target) throw new Error(`Example mark Action target was not found: ${action.target}`);
    target.textContent = action.text;
    return true;
  }
}

setupPage("examples", ExamplesPage);
