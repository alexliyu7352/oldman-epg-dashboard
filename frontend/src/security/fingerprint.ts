/**
 * 这个 Demo 的指纹发送器。
 *
 * 密钥在构建期进包（见 `vite.config.ts` 的 `fingerprintKeyShares`），拆成两段、运行时
 * 异或还原，所以产物里没有一段连续的密钥。这只提高成本：拿到 bundle 跑一遍照样能还原，
 * 示例页上也是这么写的。真实部署把自己的密钥用 `OLDMAN_FINGERPRINT_KEY` 传进构建，并和
 * `web.security.fingerprint.aes_secret_key` 保持一致。
 */

import { createFingerprintSender, type FingerprintSender } from "oldman-web/core";

declare const __OLDMAN_FINGERPRINT_SHARES__: { s1: number[]; s2: number[] };

function assembleKey(): Uint8Array {
  const { s1, s2 } = __OLDMAN_FINGERPRINT_SHARES__;
  return Uint8Array.from(s1, (byte, index) => byte ^ s2[index]!);
}

let sender: FingerprintSender | null = null;

/** 全站共用一个发送器，这样载荷缓存和 visitor id 只算一次。 */
export function fingerprintSender(): FingerprintSender {
  sender ??= createFingerprintSender({ key: assembleKey() });
  return sender;
}
