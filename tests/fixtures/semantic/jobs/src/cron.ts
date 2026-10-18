import cron from "node-cron";
import { reportQueue } from "./queues";

cron.schedule("0 * * * *", async () => {
  await reportQueue.add("build", {});
});
