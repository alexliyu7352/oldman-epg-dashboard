import { Component } from "oldman-web/core";

export class OldmanPopover extends Component {
  override async mount(): Promise<void> {
    for (const trigger of this.$$<HTMLElement>("[data-om-popover]")) {
      const label = trigger.getAttribute("data-om-popover") || trigger.getAttribute("aria-label");
      if (label && !trigger.getAttribute("title")) trigger.setAttribute("title", label);
    }
  }
}
