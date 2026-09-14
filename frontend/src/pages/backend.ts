import { setupPage } from "oldman-web/core";
import { BasePage } from "./base-page";

/**
 * 后端真实业务页面入口，复用 Dashboard 外壳生命周期与 Oldman 组件挂载能力。
 */
export class BackendPage extends BasePage {}

setupPage("backend", BackendPage);
