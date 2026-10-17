import { db } from "@/lib/db";
import { products } from "@/db/schema";

setInterval(async () => {
  const res = await fetch("https://api.example-warehouse.test/v1/stock");
  const stock = await res.json();
  await db.update(products).set({ stock: stock.total });
}, 60000);
