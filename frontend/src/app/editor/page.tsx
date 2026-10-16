import { redirect } from "next/navigation";

export default function EditorRedirect() {
  redirect("/editor/new");
}
