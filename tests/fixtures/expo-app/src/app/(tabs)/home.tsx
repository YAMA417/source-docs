import { router } from "expo-router";
import { supabase } from "../../lib/supabase";

export default function Home() {
  async function load() {
    const { data } = await supabase.from("profiles").select("*");
    await supabase.functions.invoke("notify", { body: { count: data?.length } });
    router.push("/settings");
  }
  return null;
}
