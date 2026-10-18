import * as z from "zod";

const routeContextSchema = z.object({
  params: z.object({
    id: z.string(),
  }),
});

export async function DELETE(req: Request, context: unknown) {
  const { params } = routeContextSchema.parse(context);
  return new Response(null, { status: 204 });
}
