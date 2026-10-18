import Link from "next/link";

export default async function Posts() {
  const res = await fetch("/api/posts");
  const posts = await res.json();
  return <Link href={`/posts/${posts[0].id}`}>詳細</Link>;
}
