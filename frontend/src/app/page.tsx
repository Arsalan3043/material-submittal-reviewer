"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getStoredTokens } from "@/lib/auth";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getStoredTokens() ? "/projects" : "/login");
  }, [router]);

  return null;
}
