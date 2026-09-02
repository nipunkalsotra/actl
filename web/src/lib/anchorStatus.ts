// Minimal structural shape both `AnchorInfo` (per-order, from the audit
// explain endpoint) and `MerchantTrustResponse.latest_checkpoint`
// (chain-wide, from /merchant/v1/trust) satisfy -- one status vocabulary,
// two different response shapes carrying it.
export interface AnchorLike {
  status: string;
}

// The backend distinguishes three structurally different states an
// `AnchorLike | null` can be in, and they must never share copy: `null`
// (no checkpoint covers this order yet -- the common case for almost
// every order, since a checkpoint only forms every audit_checkpoint_every
// entries), "unanchored" (checkpointed, but no on-chain tx yet -- the
// permanent state under the default ANCHOR_PROVIDER=noop), and
// "conflict" (a real integrity failure: the on-chain root disagreed with
// the local audit chain). Conflating any of these into one reassuring
// message would misrepresent a genuine problem as "nothing to see yet".
export type AnchorTone = "pending" | "unanchored" | "anchored" | "conflict";

export interface AnchorDescription {
  tone: AnchorTone;
  headline: string;
  detail?: string;
}

export function describeAnchorStatus(anchor: AnchorLike | null): AnchorDescription {
  if (anchor === null) {
    return {
      tone: "pending",
      headline: "Awaiting the next audit checkpoint",
      detail: "This order hasn't crossed a checkpoint boundary yet -- checkpoints form automatically as the audit chain grows.",
    };
  }
  if (anchor.status === "conflict") {
    return {
      tone: "conflict",
      headline: "Anchor conflict detected",
      detail: "The on-chain root for this checkpoint does not match the local audit chain. This has been flagged and is not a normal or expected state.",
    };
  }
  if (anchor.status === "anchored") {
    return { tone: "anchored", headline: "Anchored on Monad Testnet" };
  }
  return {
    tone: "unanchored",
    headline: "This checkpoint hasn't been anchored to Monad Testnet yet",
    detail: "Anchoring is optional and asynchronous -- the offline-verifiable hash chain is the primary proof regardless.",
  };
}
