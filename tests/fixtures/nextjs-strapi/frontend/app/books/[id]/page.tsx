export default async function BookPage({ params }: { params: { id: string } }) {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE}/api/books/${params.id}`);
  return <article>{(await res.json()).data.title}</article>;
}
