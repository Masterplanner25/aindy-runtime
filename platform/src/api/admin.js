import { authRequest, unwrapEnvelope } from "@aindy/ui-kit";

const BASE = "/platform/admin";

export function listUsers() {
  return authRequest(`${BASE}/users`, { method: "GET" }).then(unwrapEnvelope);
}

export function promoteUser(userId) {
  return authRequest(`${BASE}/users/${userId}/promote`, { method: "POST" }).then(unwrapEnvelope);
}
