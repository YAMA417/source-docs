import { postCreateSchema } from "@/lib/validations/post";

export async function GET() {
  return Response.json([]);
}

export async function POST(req: Request) {
  const json = await req.json();
  const body = postCreateSchema.parse(json);
  return Response.json(body, { status: 201 });
}
