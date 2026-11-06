import {
  DEFAULT_DISPLAY_CONFIG,
  type CaptionSegment,
  type DisplayConfig,
  type EngineStatus,
  type ServerMessage,
} from "@captions/protocol";

/** Keep a bounded buffer of finals in the display (server owns full history). */
const MAX_BUFFER = 200;

/**
 * Reactive caption state. Applies protocol messages to a renderable view:
 * a buffer of finalized segments plus the live (un-finalized) partial.
 */
export class CaptionStore {
  segments = $state<CaptionSegment[]>([]);
  partial = $state<CaptionSegment | null>(null);
  config = $state<DisplayConfig>(DEFAULT_DISPLAY_CONFIG);
  status = $state<EngineStatus | null>(null);

  apply = (msg: ServerMessage): void => {
    switch (msg.type) {
      case "partial":
        // Ignore blank/whitespace partials so they can't wipe a good line.
        if (msg.segment.text.trim()) this.partial = msg.segment;
        break;
      case "final":
        // A finalize still clears its partial, but a blank final is never
        // buffered (it would eat a maxLines slot and look like the screen clearing).
        if (this.partial?.id === msg.segment.id) this.partial = null;
        if (!msg.segment.text.trim()) break;
        {
          // A final can arrive twice (history replay racing the live stream).
          const i = this.segments.findIndex((s) => s.id === msg.segment.id);
          if (i >= 0) this.segments[i] = msg.segment;
          else this.segments.push(msg.segment);
        }
        if (this.segments.length > MAX_BUFFER) {
          this.segments = this.segments.slice(-MAX_BUFFER);
        }
        break;
      case "clear":
        this.segments = [];
        this.partial = null;
        break;
      case "config":
        this.config = msg.config;
        break;
      case "status":
        this.status = msg.status;
        break;
      case "history":
        this.segments = msg.segments.slice(-MAX_BUFFER);
        break;
    }
  };

  /** Lines to render: trailing finals plus the partial, bounded by maxLines. */
  get lines(): { key: string; text: string; partial: boolean }[] {
    // Defensive: never render an empty line (e.g. a blank slipping in via history).
    // Keys come from segment ids, never the text: the same words twice on
    // screen ("Thank you.") would otherwise collide, and a duplicate key breaks
    // the page's updates (layout changes stop applying until a reload).
    const out: { key: string; text: string; partial: boolean }[] = this.segments
      .filter((s) => s.text.trim() !== "")
      .map((s, i) => ({ key: `f:${s.id}:${i}`, text: s.text, partial: false }));
    if (this.partial && this.config.showPartial && this.partial.text.trim()) {
      out.push({ key: `p:${this.partial.id}`, text: this.partial.text, partial: true });
    }
    return out.slice(-this.config.maxLines);
  }
}
