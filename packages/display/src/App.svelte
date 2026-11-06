<script lang="ts">
  import { onMount } from "svelte";
  import type { Background } from "@captions/protocol";
  import { CaptionStore } from "./captionStore.svelte.js";
  import { createSourceFromUrl, type CaptionSource } from "./sources/index.js";

  const store = new CaptionStore();
  let source: CaptionSource;

  onMount(() => {
    source = createSourceFromUrl();
    source.connect(store.apply);
    return () => source.disconnect();
  });

  function backgroundCss(bg: Background): string {
    switch (bg.kind) {
      case "transparent":
        return "transparent";
      case "solid":
      case "chroma":
        return bg.color;
    }
  }

  const justify = $derived(
    store.config.position === "top"
      ? "flex-start"
      : store.config.position === "center"
        ? "center"
        : "flex-end",
  );
</script>

<div
  class="stage"
  style:background={backgroundCss(store.config.background)}
  style:justify-content={justify}
  style:--cap-color={store.config.color}
  style:--cap-font={store.config.fontFamily}
  style:--cap-size="{store.config.fontSize}vh"
  style:--cap-align={store.config.textAlign}
  style:--cap-transform={store.config.uppercase ? "uppercase" : "none"}
  style:padding="{store.config.marginY}vh {store.config.marginX}vw"
>
  <div class="captions">
    {#each store.lines as line (line.text + line.partial)}
      <div class="line" class:partial={line.partial}>{line.text}</div>
    {/each}
  </div>
</div>

<style>
  .stage {
    width: 100vw;
    height: 100vh;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    /* padding comes from config (marginY / marginX safe-area insets) */
  }
  .captions {
    width: 100%;
    text-align: var(--cap-align);
  }
  .line {
    color: var(--cap-color);
    font-family: var(--cap-font);
    font-size: var(--cap-size);
    line-height: 1.25;
    font-weight: 700;
    text-transform: var(--cap-transform);
    /* Outline for legibility over busy backgrounds (transparent/keyed). */
    text-shadow:
      0 0 0.5vh rgba(0, 0, 0, 0.9),
      0 0.2vh 0.4vh rgba(0, 0, 0, 0.7);
    transition: opacity 0.15s ease;
  }
  .line.partial {
    opacity: 0.6;
  }
</style>
