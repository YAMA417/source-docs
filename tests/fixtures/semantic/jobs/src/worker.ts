import { Worker } from "bullmq";
import { db } from "./db";
import { reports } from "./db/schema";
import { bus } from "./events";

new Worker("reports", async () => {
  await db.insert(reports).values({ body: "x" });
  bus.emit("report.created", {});
});
