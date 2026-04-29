"use server";

import { revalidatePath } from "next/cache";
import { gateway } from "@/lib/gateway";
import type { CreateAppRequest } from "@/lib/types";

export type DeployResult =
  | { ok: true; app_id: string }
  | { ok: false; error: string };

export async function deployEndpoint(input: CreateAppRequest): Promise<DeployResult> {
  try {
    const res = await gateway.createApp(input);
    revalidatePath("/serverless");
    return { ok: true, app_id: res.app_id };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function deleteEndpoint(appId: string): Promise<DeployResult> {
  try {
    await gateway.deleteApp(appId);
    revalidatePath("/serverless");
    return { ok: true, app_id: appId };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
