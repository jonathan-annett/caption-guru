import { mount } from "svelte";
import Control from "./Control.svelte";
import "./control.css";

const target = document.getElementById("app");
if (!target) throw new Error("missing #app mount target");

export default mount(Control, { target });
