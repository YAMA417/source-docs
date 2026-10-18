import * as z from "zod";

export const postCreateSchema = z.object({
  title: z.string().min(3).max(128),
  // 本文は任意
  content: z.string().optional(),
  status: z.enum(["draft", "published"]).default("draft"),
});
