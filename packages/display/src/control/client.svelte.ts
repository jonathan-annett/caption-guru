import {
  DEFAULT_DISPLAY_CONFIG,
  safeParseServerMessage,
  type CaptionSegment,
  type ClientMessage,
  type DisplayConfig,
  type EngineStatus,
} from "@captions/protocol";

/** What the desktop server's /api/state reports. */
export interface ServerState {
  demo: boolean;
  running: boolean;
  engine: string;
  model: string;
  mic: number | null;
  dictionary: string[];
  engines: { id: string; label: string }[];
  models: Record<string, string[]>;
  mics: { index: number; name: string; default: boolean }[];
  screens: { index: number; width: number | null; height: number | null }[];
  monitor: number;
  settingsPath: string | null;
}

/**
 * Two-way link to the desktop server for the settings page: mirrors config,
 * status and captions from /ws, sends control messages back, and wraps the
 * small /api for things that aren't part of the caption protocol.
 */
export class ControlClient {
  connected = $state(false);
  config = $state<DisplayConfig>({ ...DEFAULT_DISPLAY_CONFIG });
  status = $state<EngineStatus>({ state: "idle" });
  finals = $state<CaptionSegment[]>([]);
  partial = $state<CaptionSegment | null>(null);
  server = $state<ServerState | null>(null);

  private ws: WebSocket | null = null;
  private retryMs = 500;
  private pending: Partial<DisplayConfig> = {};
  private flushTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly base = "") {}

  connect(): void {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.retryMs = 500;
      this.flush(); // anything changed while we were reconnecting
      void this.refresh();
    };
    ws.onclose = () => {
      this.connected = false;
      setTimeout(() => this.connect(), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, 5000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      const msg = safeParseServerMessage(ev.data);
      if (!msg) return;
      switch (msg.type) {
        case "config":
          // Keep any edits still waiting to be sent so a slider doesn't jump back.
          this.config = { ...msg.config, ...this.pending };
          break;
        case "status":
          this.status = msg.status;
          if (this.server) this.server.running = msg.status.state !== "idle";
          break;
        case "history":
          this.finals = msg.segments.slice(-50);
          break;
        case "final":
          if (this.partial?.id === msg.segment.id) this.partial = null;
          if (msg.segment.text.trim())
            this.finals = [...this.finals.filter((s) => s.id !== msg.segment.id).slice(-49), msg.segment];
          break;
        case "partial":
          if (msg.segment.text.trim()) this.partial = msg.segment;
          break;
        case "clear":
          this.finals = [];
          this.partial = null;
          break;
      }
    };
  }

  send(msg: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  /** Apply a display change locally at once; batch the network send. */
  setConfig(patch: Partial<DisplayConfig>): void {
    this.config = { ...this.config, ...patch };
    this.pending = { ...this.pending, ...patch };
    if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        this.flush();
      }, 60);
    }
  }

  private flush(): void {
    // Only drop pending edits once they've actually gone out.
    if (!Object.keys(this.pending).length || this.ws?.readyState !== WebSocket.OPEN) return;
    const config = this.pending;
    this.pending = {};
    this.send({ type: "setConfig", config });
  }

  command(command: "start" | "stop" | "clear"): void {
    this.send({ type: "command", command });
  }

  async refresh(): Promise<void> {
    try {
      const r = await fetch(`${this.base}/api/state`);
      if (r.ok) this.server = (await r.json()) as ServerState;
    } catch {
      /* server restarting; the socket reconnect will retry */
    }
  }

  async post(path: string, body: unknown = {}): Promise<{ ok: boolean; error?: string }> {
    try {
      const r = await fetch(`${this.base}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        return { ok: false, error: (detail as { detail?: string }).detail ?? r.statusText };
      }
      await this.refresh();
      return { ok: true };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }
}
