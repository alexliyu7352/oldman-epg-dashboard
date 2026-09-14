import { Component } from "oldman-web/core";

/** Search the installed icon reference and copy exact class names. */
export class IconCatalog extends Component {
  static readonly componentName = "icon-catalog";

  override async mount(): Promise<void> {
    const search = this.$<HTMLInputElement>("[data-example-icon-search]");
    if (search) this.listen(search, "input", () => this.filter(search.value));
    this.on("click", "[data-example-icon-item]", (_event, item) => {
      if (item instanceof HTMLElement) void this.copy(item.dataset.iconClass ?? "");
    });
  }

  private filter(query: string): void {
    const normalized = query.trim().toLocaleLowerCase();
    let visible = 0;
    for (const item of this.$$<HTMLElement>("[data-example-icon-item]")) {
      item.hidden = normalized !== "" && !(item.dataset.iconClass ?? "").toLocaleLowerCase().includes(normalized);
      if (!item.hidden) visible += 1;
    }
    const empty = this.$<HTMLElement>("[data-example-icon-empty]");
    if (empty) empty.hidden = visible !== 0;
  }

  private async copy(iconClass: string): Promise<void> {
    if (!iconClass) return;
    const status = this.$<HTMLElement>("[data-example-icon-status]");
    try {
      await navigator.clipboard.writeText(iconClass);
      if (status) status.textContent = `${this.i18n.t("Copied")}: ${iconClass}`;
    } catch (error) {
      this.logger.error("Unable to copy icon class", error);
      if (status) status.textContent = this.i18n.t("Copy failed");
    }
  }
}
