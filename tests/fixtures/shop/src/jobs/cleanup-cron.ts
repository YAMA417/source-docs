import cron from "node-cron";
import { eq } from "drizzle-orm";
import { db } from "@/lib/db";
import { orders } from "@/db/schema";

cron.schedule("0 3 * * *", async () => {
  await db.delete(orders).where(eq(orders.status, "cancelled"));
});
