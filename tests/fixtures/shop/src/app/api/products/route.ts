import { db } from "@/lib/db";
import { products } from "@/db/schema";

export async function GET() {
  const rows = await db.select().from(products);
  return Response.json(rows);
}
