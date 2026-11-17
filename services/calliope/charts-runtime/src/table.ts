import * as core from "@tanstack/table-core";
import { storeReactivityBindings } from "@tanstack/table-core/store-reactivity-bindings";
import versions from "../package.json";

const api = Object.freeze({ ...core, storeReactivityBindings, version: versions.dependencies["@tanstack/table-core"] });
declare global { interface Window { RVBBIT_TABLE: typeof api; } }
window.RVBBIT_TABLE = api;
