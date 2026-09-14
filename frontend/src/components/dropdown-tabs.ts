import { Component } from "oldman-web/core";

export class DropdownTabs extends Component {
  override async mount(): Promise<void> {
    for (const trigger of this.$$<HTMLAnchorElement>("[data-om-tab-target]")) {
      this.listen(trigger, "click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.activate(trigger);
      });
    }
  }

  private activate(trigger: HTMLAnchorElement): void {
    const targetSelector = trigger.dataset.omTabTarget || trigger.getAttribute("href");
    if (!targetSelector) return;
    const panel = this.root.querySelector<HTMLElement>(targetSelector);
    if (!panel) return;

    for (const sibling of this.root.querySelectorAll<HTMLElement>("[data-om-tab-target]")) {
      sibling.classList.toggle("active", sibling === trigger);
      sibling.setAttribute("aria-selected", String(sibling === trigger));
    }
    for (const siblingPanel of this.root.querySelectorAll<HTMLElement>("[data-om-tab-panel]")) {
      const active = siblingPanel === panel;
      siblingPanel.classList.toggle("hidden", !active);
      siblingPanel.hidden = !active;
    }
  }
}
