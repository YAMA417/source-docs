import { supabase } from "../lib/supabase";

export default function Settings() {
  async function save(name: string) {
    await supabase.from("profiles").update({ name }).eq("id", 1);
    await supabase.auth.signOut();
  }
  return null;
}
