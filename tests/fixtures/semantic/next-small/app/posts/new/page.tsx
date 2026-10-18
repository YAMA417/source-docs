"use client";
import { useRouter } from "next/navigation";

export default function NewPost() {
  const router = useRouter();
  async function submit(title: string) {
    await fetch("/api/posts", { method: "POST", body: JSON.stringify({ title }) });
    router.push("/posts");
  }
  return null;
}
