export async function POST(req: Request) {
  const body = await req.json();
  return fetch(`${process.env.STRAPI_URL}/api/books`, { method: "POST", body: JSON.stringify({ data: body }) });
}
