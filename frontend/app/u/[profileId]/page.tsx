import { Suspense } from "react";
import { PublicProfile } from "@/components/public-profile";

type Props = { params: Promise<{ profileId: string }> };
async function ProfileRoute({ params }: Props) {
  const { profileId } = await params;
  return <PublicProfile key={profileId} profileId={profileId} />;
}

export default function Page({ params }: Props) {
  return <Suspense fallback={<p className="p-8 text-sm text-zinc-400">Opening public profile…</p>}><ProfileRoute params={params} /></Suspense>;
}
