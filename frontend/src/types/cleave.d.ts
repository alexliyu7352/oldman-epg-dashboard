declare module "cleave.js" {
  export type CleaveDatePattern = "d" | "m" | "Y" | "y";
  export type CleaveTimePattern = "h" | "m" | "s";
  export type CleaveNumeralThousandsGroupStyle = "thousand" | "lakh" | "wan" | "none";

  export interface CleaveOptions {
    blocks?: number[];
    date?: boolean;
    datePattern?: CleaveDatePattern[];
    delimiter?: string;
    delimiters?: string[];
    numeral?: boolean;
    numeralThousandsGroupStyle?: CleaveNumeralThousandsGroupStyle;
    prefix?: string;
    time?: boolean;
    timePattern?: CleaveTimePattern[];
    uppercase?: boolean;
  }

  export default class Cleave {
    /** 创建输入格式化实例。 */
    constructor(element: string | HTMLInputElement, options: CleaveOptions);

    /** 销毁实例并解除事件绑定。 */
    destroy(): void;

    /** 返回去掉格式化符号后的原始值。 */
    getRawValue(): string;

    /** 写入原始值并重新格式化显示值。 */
    setRawValue(value: string): void;
  }
}
