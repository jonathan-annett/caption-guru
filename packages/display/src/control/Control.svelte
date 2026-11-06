<script lang="ts">
  import { onMount } from "svelte";
  import type { Background } from "@captions/protocol";
  import { ControlClient } from "./client.svelte.js";

  const client = new ControlClient();
  onMount(() => client.connect());

  const cfg = $derived(client.config);
  const srv = $derived(client.server);

  // ---- look ------------------------------------------------------------------

  const FONTS: { label: string; value: string }[] = [
    { label: "Inter / Helvetica (default)", value: "'Inter', 'Helvetica Neue', Arial, system-ui, sans-serif" },
    { label: "Arial", value: "Arial, Helvetica, sans-serif" },
    { label: "Segoe UI", value: "'Segoe UI', system-ui, sans-serif" },
    { label: "Verdana", value: "Verdana, Geneva, sans-serif" },
    { label: "Tahoma", value: "Tahoma, Verdana, sans-serif" },
    { label: "Trebuchet MS", value: "'Trebuchet MS', sans-serif" },
    { label: "Georgia (serif)", value: "Georgia, 'Times New Roman', serif" },
    { label: "Consolas (mono)", value: "Consolas, 'Courier New', monospace" },
  ];
  const fontIsPreset = $derived(FONTS.some((f) => f.value === cfg.fontFamily));
  let customFont = $state("");

  const bgColor = $derived(cfg.background.kind === "transparent" ? "#000000" : cfg.background.color);
  function setBackground(kind: Background["kind"], color?: string) {
    if (kind === "transparent") client.setConfig({ background: { kind } });
    else
      client.setConfig({
        background: { kind, color: color ?? (kind === "chroma" ? "#00b140" : "#000000") },
      });
  }

  // ---- preview -----------------------------------------------------------------

  const screen = $derived(srv?.screens.find((s) => s.index === srv?.monitor));
  const aspect = $derived(screen?.width && screen?.height ? screen.width / screen.height : 16 / 9);
  const VW = 1920;
  let previewWidth = $state(640);

  // ---- dictionary ----------------------------------------------------------------

  let dictText = $state("");
  let dictSaved = $state<string | null>(null);
  let dictLoaded = false;
  $effect(() => {
    if (srv && !dictLoaded) {
      dictText = srv.dictionary.join("\n");
      dictLoaded = true;
    }
  });
  async function saveDictionary() {
    const terms = dictText.split(/[\n,]/).map((t) => t.trim()).filter(Boolean);
    const r = await client.post("/api/dictionary", { terms });
    dictSaved = r.ok ? `Saved ${terms.length} term${terms.length === 1 ? "" : "s"}` : `Failed: ${r.error}`;
  }

  // ---- engine ----------------------------------------------------------------------

  let engine = $state("");
  let model = $state("");
  let mic = $state("");
  let engineLoaded = false;
  let engineMsg = $state<string | null>(null);
  $effect(() => {
    if (srv && !engineLoaded) {
      engine = srv.engine;
      model = srv.model;
      mic = srv.mic == null ? "" : String(srv.mic);
      engineLoaded = true;
    }
  });
  const modelChoices = $derived(srv?.models[engine] ?? []);
  function onEngineChange() {
    const choices = srv?.models[engine] ?? [];
    if (!choices.includes(model)) model = engine === "sherpa-onnx" ? "nemotron-en-560ms" : "base.en";
  }
  const engineDirty = $derived(
    !!srv && (engine !== srv.engine || model !== srv.model || mic !== (srv.mic == null ? "" : String(srv.mic))),
  );
  async function applyEngine() {
    engineMsg = "Restarting captions…";
    const r = await client.post("/api/engine", { engine, model, mic: mic === "" ? null : Number(mic) });
    engineMsg = r.ok ? null : `Failed: ${r.error}`;
  }

  async function setMonitor(e: Event) {
    await client.post("/api/launch", { monitor: Number((e.target as HTMLSelectElement).value) });
  }

  async function resetLook() {
    await client.post("/api/display/reset");
  }

  // ---- status ------------------------------------------------------------------------

  const statusText = $derived.by(() => {
    if (!client.connected) return "Not connected — is Caption Guru running?";
    const s = client.status;
    if (s.state === "listening") return `Listening · ${s.model ?? ""}${s.device ? " · " + s.device : ""}`;
    if (s.state === "loading") return s.message ?? "Loading model…";
    if (s.state === "error") return `Error: ${s.message ?? "unknown"}`;
    return "Stopped";
  });
  const running = $derived(client.status.state === "listening" || client.status.state === "loading");
  const stateClass = $derived(client.connected ? client.status.state : "offline");
</script>

<div class="app">
  <header>
    <div class="brand">
      <span class="logo">CC</span>
      <h1>Caption Guru</h1>
      {#if srv?.demo}<span class="tag">demo mode</span>{/if}
    </div>
    <span class="status {stateClass}"><i></i>{statusText}</span>
    <div class="actions">
      {#if running}
        <button class="danger" onclick={() => client.command("stop")}>Stop captions</button>
      {:else}
        <button class="primary" onclick={() => client.command("start")} disabled={!client.connected}>Start captions</button>
      {/if}
      <button onclick={() => client.command("clear")} disabled={!client.connected}>Clear screen</button>
    </div>
  </header>

  <div class="layout">
    <section class="stagecol">
      <div class="card">
        <div class="cardhead">
          <h2>Output preview</h2>
          <span class="muted">
            {#if screen?.width}Monitor {srv?.monitor} · {screen.width}×{screen.height}{:else}16:9{/if}
          </span>
        </div>
        <div class="preview" class:checker={cfg.background.kind === "transparent"} bind:clientWidth={previewWidth} style:aspect-ratio={aspect}>
          {#if client.connected}
            <iframe
              title="Caption output preview"
              src="./index.html?source=ws"
              style:width="{VW}px"
              style:height="{VW / aspect}px"
              style:transform="scale({previewWidth / VW})"
            ></iframe>
          {/if}
        </div>
        <p class="muted small">This is the same page that's on air — changes show here and on the output at once.</p>
      </div>

      <div class="card">
        <div class="cardhead"><h2>Transcript</h2>
          <span class="exports">Export
            <a href="/export?format=txt" download>TXT</a>
            <a href="/export?format=srt" download>SRT</a>
            <a href="/export?format=vtt" download>VTT</a>
          </span>
        </div>
        <div class="transcript">
          {#each client.finals.slice(-8) as seg (seg.id)}
            <p>{seg.text}</p>
          {/each}
          {#if client.partial}<p class="partial">{client.partial.text}</p>{/if}
          {#if !client.finals.length && !client.partial}<p class="muted">Captions will appear here.</p>{/if}
        </div>
      </div>
    </section>

    <section class="settings">
      <div class="card">
        <div class="cardhead"><h2>Position</h2><button class="link" onclick={resetLook}>Reset look</button></div>
        <div class="row">
          <span class="lbl">Placement</span>
          <div class="seg">
            {#each [["top", "Top"], ["center", "Middle"], ["bottom", "Bottom"]] as [v, l]}
              <button class:on={cfg.position === v} onclick={() => client.setConfig({ position: v as "top" })}>{l}</button>
            {/each}
          </div>
        </div>
        <div class="row">
          <span class="lbl">Alignment</span>
          <div class="seg">
            {#each [["left", "Left"], ["center", "Centre"], ["right", "Right"]] as [v, l]}
              <button class:on={cfg.textAlign === v} onclick={() => client.setConfig({ textAlign: v as "left" })}>{l}</button>
            {/each}
          </div>
        </div>
        <label class="row">
          <span class="lbl">Distance from edge</span>
          <input type="range" min="0" max="40" step="0.5" value={cfg.marginY}
            oninput={(e) => client.setConfig({ marginY: Number(e.currentTarget.value) })} />
          <span class="val">{cfg.marginY}%</span>
        </label>
        <label class="row">
          <span class="lbl">Side margins</span>
          <input type="range" min="0" max="40" step="0.5" value={cfg.marginX}
            oninput={(e) => client.setConfig({ marginX: Number(e.currentTarget.value) })} />
          <span class="val">{cfg.marginX}%</span>
        </label>
        <label class="row">
          <span class="lbl">Captions on screen</span>
          <input type="range" min="1" max="6" step="1" value={cfg.maxLines}
            oninput={(e) => client.setConfig({ maxLines: Number(e.currentTarget.value) })} />
          <span class="val">{cfg.maxLines}</span>
        </label>
      </div>

      <div class="card">
        <div class="cardhead"><h2>Text</h2></div>
        <label class="row">
          <span class="lbl">Size</span>
          <input type="range" min="2" max="16" step="0.25" value={cfg.fontSize}
            oninput={(e) => client.setConfig({ fontSize: Number(e.currentTarget.value) })} />
          <span class="val">{cfg.fontSize}%</span>
        </label>
        <label class="row">
          <span class="lbl">Font</span>
          <select value={fontIsPreset ? cfg.fontFamily : "custom"}
            onchange={(e) => {
              const v = e.currentTarget.value;
              if (v === "custom") customFont = fontIsPreset ? "" : cfg.fontFamily;
              else client.setConfig({ fontFamily: v });
            }}>
            {#each FONTS as f}<option value={f.value}>{f.label}</option>{/each}
            <option value="custom">Other installed font…</option>
          </select>
        </label>
        {#if !fontIsPreset || customFont !== ""}
          <label class="row">
            <span class="lbl">Font name</span>
            <input type="text" placeholder="e.g. Calibri" value={fontIsPreset ? customFont : cfg.fontFamily}
              onchange={(e) => { const v = e.currentTarget.value.trim(); if (v) { customFont = v; client.setConfig({ fontFamily: v }); } }} />
          </label>
        {/if}
        <label class="row">
          <span class="lbl">Colour</span>
          <input type="color" value={cfg.color} oninput={(e) => client.setConfig({ color: e.currentTarget.value })} />
          <span class="val mono">{cfg.color}</span>
        </label>
        <label class="row check">
          <input type="checkbox" checked={cfg.uppercase} onchange={(e) => client.setConfig({ uppercase: e.currentTarget.checked })} />
          <span>ALL CAPS</span>
        </label>
        <label class="row check">
          <input type="checkbox" checked={cfg.showPartial} onchange={(e) => client.setConfig({ showPartial: e.currentTarget.checked })} />
          <span>Show words as they're spoken <span class="muted">(dimmed until the sentence is final)</span></span>
        </label>
      </div>

      <div class="card">
        <div class="cardhead"><h2>Background</h2></div>
        <div class="row">
          <span class="lbl">Type</span>
          <div class="seg">
            <button class:on={cfg.background.kind === "solid"} onclick={() => setBackground("solid")}>Solid</button>
            <button class:on={cfg.background.kind === "chroma"} onclick={() => setBackground("chroma")}>Chroma key</button>
            <button class:on={cfg.background.kind === "transparent"} onclick={() => setBackground("transparent")}>Transparent</button>
          </div>
        </div>
        {#if cfg.background.kind !== "transparent"}
          <label class="row">
            <span class="lbl">Colour</span>
            <input type="color" value={bgColor} oninput={(e) => setBackground(cfg.background.kind, e.currentTarget.value)} />
            <span class="swatches">
              {#each ["#000000", "#00b140", "#00ff00", "#0047bb", "#ff00ff"] as c}
                <button class="sw" style:background={c} title={c} aria-label={c} onclick={() => setBackground(cfg.background.kind, c)}></button>
              {/each}
            </span>
          </label>
        {:else}
          <p class="muted small">For OBS / vMix browser sources. Over HDMI it shows as black. Restart the app for a see-through window.</p>
        {/if}
      </div>

      <div class="card">
        <div class="cardhead"><h2>Custom words</h2></div>
        <textarea rows="4" bind:value={dictText} placeholder="Names, jargon, acronyms — one per line or comma separated"></textarea>
        <div class="row end">
          {#if dictSaved}<span class="muted small">{dictSaved}</span>{/if}
          <button onclick={saveDictionary} disabled={!client.connected}>Save words</button>
        </div>
        <p class="muted small">Write names in normal case ("Steve Gibson"). Applies from the next sentence.</p>
      </div>

      <div class="card">
        <div class="cardhead"><h2>Speech engine</h2></div>
        {#if srv?.demo}
          <p class="muted small">Demo mode shows scripted captions. Start Caption Guru normally to choose an engine and microphone.</p>
        {:else if srv}
          <label class="row">
            <span class="lbl">Microphone</span>
            <select bind:value={mic}>
              <option value="">System default</option>
              {#each srv.mics as m (m.index)}
                <option value={String(m.index)}>{m.name}{m.default ? " (default)" : ""}</option>
              {/each}
            </select>
          </label>
          <label class="row">
            <span class="lbl">Engine</span>
            <select bind:value={engine} onchange={onEngineChange}>
              {#each srv.engines as e (e.id)}<option value={e.id}>{e.label}</option>{/each}
            </select>
          </label>
          <label class="row">
            <span class="lbl">Model</span>
            <input type="text" list="models" bind:value={model} />
            <datalist id="models">{#each modelChoices as m}<option value={m}></option>{/each}</datalist>
          </label>
          <p class="muted small">
            {#if engine === "sherpa-onnx"}Smaller ms = lower delay, larger = more accurate. First use downloads ~460 MB.
            {:else}Bigger models are more accurate but slower. First use downloads the model.{/if}
          </p>
          <div class="row end">
            {#if engineMsg}<span class="muted small">{engineMsg}</span>{/if}
            <button class="primary" onclick={applyEngine} disabled={!engineDirty}>Apply &amp; restart captions</button>
          </div>
        {/if}
      </div>

      <div class="card">
        <div class="cardhead"><h2>Output</h2></div>
        {#if srv && srv.screens.length > 1}
          <label class="row">
            <span class="lbl">Caption monitor</span>
            <select value={String(srv.monitor)} onchange={setMonitor}>
              {#each srv.screens as s (s.index)}
                <option value={String(s.index)}>Monitor {s.index}{s.width ? ` · ${s.width}×${s.height}` : ""}</option>
              {/each}
            </select>
          </label>
          <p class="muted small">Takes effect next time Caption Guru starts.</p>
        {:else}
          <p class="muted small">Only one monitor detected. Connect your HDMI output and restart to choose it here.</p>
        {/if}
        <p class="muted small">Browser source for OBS / vMix: <code>{location.origin}/?source=ws</code></p>
        {#if srv?.settingsPath}<p class="muted small">Settings are saved automatically to <code>{srv.settingsPath}</code></p>{/if}
      </div>
    </section>
  </div>
</div>
