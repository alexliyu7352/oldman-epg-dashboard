import { Component } from "oldman-web/core";

export class OldmanTooltip extends Component {
  override async mount(): Promise<void> {
    for (const trigger of this.$$<HTMLElement>("[data-om-tooltip]")) {
      const label = trigger.getAttribute("data-om-tooltip") || trigger.getAttribute("aria-label") || trigger.getAttribute("title");
      if (!label) continue;
      trigger.setAttribute("title", label);
    }
  }
}
