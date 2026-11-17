import * as core from "@tanstack/virtual-core";
import versions from "../package.json";

const api = Object.freeze({ ...core, version: versions.dependencies["@tanstack/virtual-core"] });
declare global { interface Window { RVBBIT_VIRTUAL: typeof api; } }
window.RVBBIT_VIRTUAL = api;
