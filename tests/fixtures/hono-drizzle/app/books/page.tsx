"use client";
export default function Books() {
  async function add(title: string) {
    await fetch("/api/books", { method: "POST", body: JSON.stringify({ title }) });
  }
  return <button onClick={() => add("x")}>追加</button>;
}
