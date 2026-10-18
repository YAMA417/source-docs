import { EventEmitter } from "node:events";

export const bus = new EventEmitter();
bus.on("report.created", () => {});
