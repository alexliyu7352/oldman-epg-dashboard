declare module "wnumb" {
  export interface WNumbOptions {
    decimals?: number;
    mark?: string;
    negativeBefore?: string;
    negative?: string;
    thousand?: string;
    prefix?: string;
    suffix?: string;
    encoder?: (value: number) => number;
    decoder?: (value: number) => number;
    edit?: (value: string, original: number) => string;
    undo?: (value: string) => string;
  }

  export interface WNumbFormatter {
    to(value: number): string;
    from(value: string): number | false;
  }

  export default function wNumb(options?: WNumbOptions): WNumbFormatter;
}
