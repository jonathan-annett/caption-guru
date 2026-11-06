/**
 * Shared caption protocol — the single source of truth for messages exchanged
 * between an ASR engine (PWA in-browser, or desktop Python server) and the
 * display / control clients.
 *
 * Transport-agnostic: the same JSON flows over a BroadcastChannel (PWA) or a
 * WebSocket (desktop). All timestamps are **seconds relative to session start**.
 *
 * Captions form an append-only, timestamped log. `final` segments carry a
 * stable `id` + `start`/`end`, so the stream is replayable — this is what makes
 * the future audience-scrollback feature a pure add-on (history replay), not a
 * schema change.
 */
import { z } from "zod";

export * from "./export.js";

/** Bumped on breaking changes to the message shapes below. */
export const PROTOCOL_VERSION = 1;

// ---------------------------------------------------------------------------
// Segments
// ---------------------------------------------------------------------------

export const WordSchema = z.object({
  text: z.string(),
  /** seconds from session start */
  start: z.number(),
  /** seconds from session start */
  end: z.number(),
  /** decoder confidence 0..1, when available */
  confidence: z.number().min(0).max(1).optional(),
});
export type Word = z.infer<typeof WordSchema>;

export const CaptionSegmentSchema = z.object({
  /** stable id; a partial keeps the same id until it is finalized */
  id: z.string(),
  text: z.string(),
  start: z.number(),
  end: z.number(),
  /** speaker label (diarization, future) */
  speaker: z.string().optional(),
  /** BCP-47 language tag of `text` */
  lang: z.string().optional(),
  /** word-level timing (WhisperX alignment / export), when available */
  words: z.array(WordSchema).optional(),
});
export type CaptionSegment = z.infer<typeof CaptionSegmentSchema>;

// ---------------------------------------------------------------------------
// Display configuration
// ---------------------------------------------------------------------------

export const BackgroundSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("transparent") }),
  z.object({ kind: z.literal("solid"), color: z.string() }),
  /** chroma-key fill for switcher keying (default green) */
  z.object({ kind: z.literal("chroma"), color: z.string() }),
]);
export type Background = z.infer<typeof BackgroundSchema>;

export const DisplayPositionSchema = z.enum(["top", "center", "bottom"]);
export type DisplayPosition = z.infer<typeof DisplayPositionSchema>;

export const DisplayModeSchema = z.enum(["rolling", "scroll"]);
export type DisplayMode = z.infer<typeof DisplayModeSchema>;

export const DisplayConfigSchema = z.object({
  fontFamily: z.string(),
  /** font size in viewport-height units (vh) so it scales with output res */
  fontSize: z.number().positive(),
  color: z.string(),
  background: BackgroundSchema,
  position: DisplayPositionSchema,
  textAlign: z.enum(["left", "center", "right"]),
  /** rolling: keep last N lines; scroll: visible line budget */
  maxLines: z.number().int().positive(),
  mode: DisplayModeSchema,
  /** show the live (un-finalized) hypothesis in a dimmed style */
  showPartial: z.boolean(),
  uppercase: z.boolean(),
  /** horizontal safe-area inset, viewport-width units (vw) */
  marginX: z.number().min(0).max(45).default(6),
  /** vertical safe-area inset (distance from top/bottom edge), vh */
  marginY: z.number().min(0).max(45).default(4),
});
export type DisplayConfig = z.infer<typeof DisplayConfigSchema>;

export const DEFAULT_DISPLAY_CONFIG: DisplayConfig = {
  fontFamily:
    "'Inter', 'Helvetica Neue', Arial, system-ui, sans-serif",
  fontSize: 6,
  color: "#ffffff",
  // Default solid black suits HDMI capture; switch to chroma/transparent per workflow.
  background: { kind: "solid", color: "#000000" },
  position: "bottom",
  textAlign: "center",
  maxLines: 2,
  mode: "rolling",
  showPartial: true,
  uppercase: false,
  marginX: 6,
  marginY: 4,
};

// ---------------------------------------------------------------------------
// Engine status
// ---------------------------------------------------------------------------

export const EngineStateSchema = z.enum([
  "idle",
  "loading", // model loading / warming up
  "listening",
  "error",
]);
export type EngineState = z.infer<typeof EngineStateSchema>;

export const EngineStatusSchema = z.object({
  state: EngineStateSchema,
  /** engine/backend identifier, e.g. "faster-whisper", "transformers.js" */
  backend: z.string().optional(),
  model: z.string().optional(),
  /** WebGPU / CUDA / Metal / CPU */
  device: z.string().optional(),
  message: z.string().optional(),
});
export type EngineStatus = z.infer<typeof EngineStatusSchema>;

// ---------------------------------------------------------------------------
// Server -> client messages
// ---------------------------------------------------------------------------

export const PartialMessageSchema = z.object({
  type: z.literal("partial"),
  segment: CaptionSegmentSchema,
});
export const FinalMessageSchema = z.object({
  type: z.literal("final"),
  segment: CaptionSegmentSchema,
});
export const ClearMessageSchema = z.object({ type: z.literal("clear") });
export const ConfigMessageSchema = z.object({
  type: z.literal("config"),
  config: DisplayConfigSchema,
});
export const StatusMessageSchema = z.object({
  type: z.literal("status"),
  status: EngineStatusSchema,
});
/** Replay of finalized segments — for late joiners and audience scrollback. */
export const HistoryMessageSchema = z.object({
  type: z.literal("history"),
  segments: z.array(CaptionSegmentSchema),
});

export const ServerMessageSchema = z.discriminatedUnion("type", [
  PartialMessageSchema,
  FinalMessageSchema,
  ClearMessageSchema,
  ConfigMessageSchema,
  StatusMessageSchema,
  HistoryMessageSchema,
]);
export type ServerMessage = z.infer<typeof ServerMessageSchema>;

// ---------------------------------------------------------------------------
// Client -> server (control) messages
// ---------------------------------------------------------------------------

export const SetConfigMessageSchema = z.object({
  type: z.literal("setConfig"),
  config: DisplayConfigSchema.partial(),
});
export const SetDictionaryMessageSchema = z.object({
  type: z.literal("setDictionary"),
  /** event-specific names/jargon to bias decoding toward */
  terms: z.array(z.string()),
});
export const ControlCommandSchema = z.object({
  type: z.literal("command"),
  command: z.enum(["start", "stop", "clear"]),
});
/** Request replayable history (since N seconds from session start). */
export const RequestHistoryMessageSchema = z.object({
  type: z.literal("requestHistory"),
  since: z.number().optional(),
});

export const ClientMessageSchema = z.discriminatedUnion("type", [
  SetConfigMessageSchema,
  SetDictionaryMessageSchema,
  ControlCommandSchema,
  RequestHistoryMessageSchema,
]);
export type ClientMessage = z.infer<typeof ClientMessageSchema>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Parse + validate an inbound server message (throws on invalid). */
export function parseServerMessage(data: unknown): ServerMessage {
  return ServerMessageSchema.parse(typeof data === "string" ? JSON.parse(data) : data);
}

/** Parse + validate an inbound client/control message (throws on invalid). */
export function parseClientMessage(data: unknown): ClientMessage {
  return ClientMessageSchema.parse(typeof data === "string" ? JSON.parse(data) : data);
}

/** Safe variant: returns null instead of throwing. */
export function safeParseServerMessage(data: unknown): ServerMessage | null {
  const json = typeof data === "string" ? safeJson(data) : data;
  const r = ServerMessageSchema.safeParse(json);
  return r.success ? r.data : null;
}

function safeJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}
