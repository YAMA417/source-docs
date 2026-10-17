import { events } from "@/events/bus";
import { db } from "@/lib/db";
import { products } from "@/db/schema";

events.on("order.paid", async () => {
  await db.update(products).set({ stock: 0 });
});
